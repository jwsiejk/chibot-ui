from __future__ import annotations

import copy
import gzip
import io
import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque, Dict, Iterable, List, Mapping, Optional, Set, Tuple

from app.admin_log import emit as admin_log_emit, get_admin_log_history
from app.obs.source_tags import FLOW_SCHEMA_VERSION

MAX_EVENTS = 5000
DEDUP_WINDOW_MS = 100
ASR_PARTIAL_TYPE = "asr_partial_first"
BARGE_IN_TYPE = "barge_in"
BARGE_PAUSE_TYPES = {"barge_pause", "barge_hold"}
BARGE_RESUME_TYPE = "barge_resume"
TTS_START_TYPE = "tts_start"
TTS_END_TYPE = "tts_end"
LLM_START_TYPE = "llm_start"
LLM_FINAL_TYPE = "llm_final"
CONFIRM_OPEN_TYPE = "confirm_open"
CONFIRM_CLOSE_TYPE = "confirm_close"

MAX_BATCH_ITEMS = 1000
MAX_BATCH_BYTES = 512 * 1024
FLOW_DROPPED_TYPE = "flow_dropped"

# Simple static blurbs with light interpolation.
BLURBS: Dict[str, str] = {
    CONFIRM_OPEN_TYPE: "Confirmation started",
    CONFIRM_CLOSE_TYPE: "Confirmation closed",
    BARGE_IN_TYPE: "Barge-in detected",
    TTS_START_TYPE: "Assistant speech started",
    TTS_END_TYPE: "Assistant speech ended",
    LLM_START_TYPE: "LLM thinking",
    LLM_FINAL_TYPE: "LLM response ready",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_meta(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if meta is None:
        return {}
    if not isinstance(meta, dict):
        raise TypeError("meta must be a mapping")
    return dict(meta)


def _words(text: str) -> List[str]:
    return [word for word in text.split() if word]


def _coerce_source_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        text = str(value).strip()
    except Exception:
        return None
    return text or None


def _resolve_event_source(who: Any, meta: Mapping[str, Any]) -> Tuple[str, bool]:
    if isinstance(meta, Mapping):
        direct = _coerce_source_text(meta.get("src"))
        if direct:
            return direct, False
        component = _coerce_source_text(meta.get("component"))
        if component:
            return component, False

    who_text = _coerce_source_text(who) or ""
    lower_who = who_text.lower()
    if lower_who in {"server", "system"}:
        return "server_core", False
    if lower_who == "client":
        return "client_ui", False
    return "unknown", True


@dataclass
class EventRecord:
    data: Dict[str, Any]
    monotonic_ts: float
    children_ids: List[str] = field(default_factory=list)
    batches: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self, children: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        payload = dict(self.data)
        parent_id = payload.get("parent_id")
        if parent_id is None:
            payload.pop("parent_id", None)
        if children is not None:
            if children:
                payload["children"] = children
        elif self.children_ids:
            payload["children"] = list(self.children_ids)
        if self.batches:
            payload["batches"] = [dict(batch) for batch in self.batches]
        return payload


@dataclass
class SessionBucket:
    session_id: str
    started_at_iso: str
    t0_monotonic: float
    seq: int = 1
    events: Deque[EventRecord] = field(default_factory=deque)
    event_index: Dict[str, EventRecord] = field(default_factory=dict)
    dedupe: Dict[Tuple[str, str, Optional[str]], Tuple[float, str]] = field(default_factory=dict)
    asr_partial_turns: Dict[str, str] = field(default_factory=dict)
    barge_paused: bool = False
    open_confirms: Dict[str, EventRecord] = field(default_factory=dict)
    open_tts: Dict[str, EventRecord] = field(default_factory=dict)
    open_llm: Dict[str, EventRecord] = field(default_factory=dict)
    tts_state: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    drop_buffer: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class FlowSessionSnapshot:
    session_id: str
    started_at_iso: Optional[str]
    levels: List[str]
    events: List[Dict[str, Any]]
    config: Optional[Dict[str, Any]]

    @property
    def event_count(self) -> int:
        return len(self.events)


class FlowStore:
    """Thread-safe, singleton flow trace store."""

    _instance: Optional["FlowStore"] = None
    _singleton_lock = threading.Lock()
    _normalization_logged = False

    _logger = logging.getLogger(__name__)

    @classmethod
    def instance(cls) -> "FlowStore":
        """Return the singleton FlowStore instance."""
        return cls()

    def __new__(cls) -> "FlowStore":
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init()
        return cls._instance

    def _init(self) -> None:
        self._lock = threading.RLock()
        self._sessions: Dict[str, SessionBucket] = {}
        self._event_sessions: Dict[str, str] = {}
        if not FlowStore._normalization_logged:
            try:
                admin_log_emit("flow_source_normalization", message="enabled")
            except Exception:
                FlowStore._logger.info("flow_source_normalization: enabled")
            else:
                FlowStore._logger.info("flow_source_normalization: enabled")
            FlowStore._normalization_logged = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def emit(
        self,
        session_id: str,
        level: str,
        phase: str,
        type_: str,
        who: str,
        meta: Optional[Dict[str, Any]] = None,
        parent_id: Optional[str] = None,
    ) -> Optional[str]:
        meta_copy = _ensure_meta(meta)
        with self._lock:
            bucket = self._ensure_bucket(session_id)
            now = time.monotonic()
            turn_id = self._extract_turn_id(meta_copy)

            suppressed = self._should_suppress(bucket, now, type_, who, turn_id)
            if suppressed is not None:
                return suppressed

            if type_ == BARGE_IN_TYPE and bucket.barge_paused:
                return None

            if type_ == ASR_PARTIAL_TYPE and turn_id:
                if turn_id in bucket.asr_partial_turns:
                    return bucket.asr_partial_turns[turn_id]

            if type_ == TTS_START_TYPE:
                key = turn_id or "__default__"
                state = bucket.tts_state.get(key)
                if state and state.get("active"):
                    return state.get("event_id")

            if type_ == TTS_END_TYPE:
                key = turn_id or "__default__"
                state = bucket.tts_state.get(key)
                if state and state.get("ended"):
                    return state.get("ended_event_id")

            record = self._create_event(
                bucket,
                now,
                level=level,
                phase=phase,
                type_=type_,
                who=who,
                meta=meta_copy,
                parent_id=parent_id,
                is_injection=False,
            )

            self._update_bucket_state(bucket, record, turn_id)
            self._inject_safety_events(bucket)
            return record.data["id"]

    def add_batch(self, session_id: str, parent_id: str, kind: str, items: Iterable[Any]) -> bool:
        items_list = list(items)
        if len(items_list) > MAX_BATCH_ITEMS:
            with self._lock:
                bucket = self._sessions.get(session_id)
                if bucket:
                    self._record_drop_notice(
                        bucket,
                        reason="batch_items",
                        meta={"parent_id": parent_id, "count": len(items_list)},
                    )
                    self._flush_drop_notices(bucket)
            raise ValueError("batch too large")
        serialized = str(items_list).encode("utf-8")
        if len(serialized) > MAX_BATCH_BYTES:
            with self._lock:
                bucket = self._sessions.get(session_id)
                if bucket:
                    self._record_drop_notice(
                        bucket,
                        reason="batch_bytes",
                        meta={"parent_id": parent_id, "bytes": len(serialized)},
                    )
                    self._flush_drop_notices(bucket)
            raise ValueError("batch exceeds size limit")

        with self._lock:
            bucket = self._sessions.get(session_id)
            if not bucket:
                return False
            record = bucket.event_index.get(parent_id)
            if not record:
                return False
            batch_payload = {"kind": kind, "items": items_list}
            record.batches.append(batch_payload)
            return True

    def add_batch_for_event(self, parent_id: str, kind: str, items: Iterable[Any]) -> bool:
        items_list = list(items)
        if len(items_list) > MAX_BATCH_ITEMS:
            with self._lock:
                session_id = self._event_sessions.get(parent_id)
                if session_id:
                    bucket = self._sessions.get(session_id)
                    if bucket:
                        self._record_drop_notice(
                            bucket,
                            reason="batch_items",
                            meta={"parent_id": parent_id, "count": len(items_list)},
                        )
                        self._flush_drop_notices(bucket)
            raise ValueError("batch too large")
        serialized = str(items_list).encode("utf-8")
        if len(serialized) > MAX_BATCH_BYTES:
            with self._lock:
                session_id = self._event_sessions.get(parent_id)
                if session_id:
                    bucket = self._sessions.get(session_id)
                    if bucket:
                        self._record_drop_notice(
                            bucket,
                            reason="batch_bytes",
                            meta={"parent_id": parent_id, "bytes": len(serialized)},
                        )
                        self._flush_drop_notices(bucket)
            raise ValueError("batch exceeds size limit")

        with self._lock:
            session_id = self._event_sessions.get(parent_id)
            if session_id is None:
                return False
            bucket = self._sessions.get(session_id)
            if not bucket:
                return False
            record = bucket.event_index.get(parent_id)
            if not record:
                return False
            batch_payload = {"kind": kind, "items": items_list}
            record.batches.append(batch_payload)
            return True

    def snapshot(
        self,
        session_id: str,
        *,
        levels: Iterable[str] = ("flow", "transition"),
        expand: str = "all",
    ) -> FlowSessionSnapshot:
        sanitized_levels: List[str] = []
        seen: Set[str] = set()
        for level in levels:
            try:
                text = str(level)
            except Exception:
                continue
            text = text.strip()
            if not text or text in seen:
                continue
            seen.add(text)
            sanitized_levels.append(text)
        if not sanitized_levels:
            sanitized_levels = ["flow", "transition"]

        with self._lock:
            bucket = self._sessions.get(session_id)
            if not bucket:
                return FlowSessionSnapshot(
                    session_id=session_id,
                    started_at_iso=None,
                    levels=list(sanitized_levels),
                    events=[],
                    config=None,
                )

            self._inject_safety_events(bucket)
            expand_policy = self._parse_expand(expand)
            level_filter = set(sanitized_levels)

            events: List[Dict[str, Any]] = []
            for record in bucket.events:
                if level_filter and record.data.get("level") not in level_filter:
                    continue
                events.append(self._format_event(bucket, record, expand_policy))

            config = self._extract_session_config(bucket)

            return FlowSessionSnapshot(
                session_id=session_id,
                started_at_iso=bucket.started_at_iso,
                levels=list(sanitized_levels),
                events=events,
                config=config,
            )

    def list(
        self,
        session_id: str,
        since_ms: Optional[int] = None,
        limit: int = 200,
        levels: Iterable[str] = ("flow", "transition"),
        expand: str = "flow",
    ) -> Dict[str, Any]:
        with self._lock:
            bucket = self._sessions.get(session_id)
            if not bucket:
                return {
                    "session_id": session_id,
                    "started_at": None,
                    "events": [],
                    "next_since_ms": since_ms or 0,
                    "hints": [],
                }
            self._inject_safety_events(bucket)
            levels_set = set(levels)
            since_value = since_ms or 0
            expand_policy = self._parse_expand(expand)

            events: List[Dict[str, Any]] = []
            count = 0
            next_since = since_value

            for record in bucket.events:
                if record.data["t_rel_ms"] < since_value:
                    continue
                if levels_set and record.data["level"] not in levels_set:
                    continue
                events.append(self._format_event(bucket, record, expand_policy))
                count += 1
                next_since = max(next_since, record.data["t_rel_ms"])
                if count >= limit:
                    break

            if events:
                next_since += 1

            hints = self._compute_hints(bucket)

            return {
                "session_id": session_id,
                "started_at": bucket.started_at_iso,
                "events": events,
                "next_since_ms": next_since,
                "hints": hints,
            }

    def sessions(
        self,
        *,
        query: Optional[str] = None,
        limit: Optional[int] = 50,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            needle = (query or "").strip().lower()
            filtered: List[Dict[str, Any]] = []
            for bucket in self._sessions.values():
                if needle and needle not in bucket.session_id.lower():
                    continue

                event_count = len(bucket.events)
                last_record = bucket.events[-1] if event_count else None
                last_rel_ms = last_record.data.get("t_rel_ms") if last_record else 0
                last_type = last_record.data.get("type") if last_record else None
                last_phase = last_record.data.get("phase") if last_record else None
                last_who = last_record.data.get("who") if last_record else None
                last_event_id = last_record.data.get("id") if last_record else None

                filtered.append(
                    {
                        "session_id": bucket.session_id,
                        "started_at": bucket.started_at_iso,
                        "event_count": event_count,
                        "last_event_ms": last_rel_ms or 0,
                        "last_type": last_type,
                        "last_phase": last_phase,
                        "last_who": last_who,
                        "last_event_id": last_event_id,
                    }
                )

            filtered.sort(
                key=lambda item: (item["last_event_ms"], item["event_count"], item["session_id"]),
                reverse=True,
            )

            if limit is not None and limit >= 0:
                return filtered[: limit or 0] if limit == 0 else filtered[:limit]
            return filtered

    def get(self, session_id: str, event_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            bucket = self._sessions.get(session_id)
            if not bucket:
                return None
            record = bucket.event_index.get(event_id)
            if not record:
                return None
            expand_policy = {"mode": "all", "ids": set()}
            return self._format_event(bucket, record, expand_policy)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _extract_session_config(self, bucket: SessionBucket) -> Optional[Dict[str, Any]]:
        for record in reversed(bucket.events):
            if record.data.get("type") != "session_config":
                continue
            meta = record.data.get("meta")
            if not isinstance(meta, dict):
                continue
            config = meta.get("config")
            if isinstance(config, dict):
                return copy.deepcopy(config)
        return None

    def _ensure_bucket(self, session_id: str) -> SessionBucket:
        bucket = self._sessions.get(session_id)
        if bucket is None:
            bucket = SessionBucket(
                session_id=session_id,
                started_at_iso=_utc_now_iso(),
                t0_monotonic=time.monotonic(),
            )
            self._sessions[session_id] = bucket
        return bucket

    def _extract_turn_id(self, meta: Dict[str, Any]) -> Optional[str]:
        turn_id = meta.get("turn_id") if isinstance(meta, dict) else None
        if turn_id is None:
            return None
        return str(turn_id)

    def _should_suppress(
        self,
        bucket: SessionBucket,
        now: float,
        type_: str,
        who: str,
        turn_id: Optional[str],
    ) -> Optional[str]:
        key = (type_, who, turn_id)
        dedupe = bucket.dedupe.get(key)
        if dedupe:
            prev_time, prev_event_id = dedupe
            if (now - prev_time) * 1000 <= DEDUP_WINDOW_MS:
                return prev_event_id
        # prune stale entries
        stale_keys = [
            k for k, (ts, _) in bucket.dedupe.items() if (now - ts) * 1000 > DEDUP_WINDOW_MS
        ]
        for stale_key in stale_keys:
            bucket.dedupe.pop(stale_key, None)
        return None

    def _create_event(
        self,
        bucket: SessionBucket,
        now: float,
        *,
        level: str,
        phase: str,
        type_: str,
        who: str,
        meta: Dict[str, Any],
        parent_id: Optional[str],
        is_injection: bool,
    ) -> EventRecord:
        event_id = f"e_{bucket.seq:05d}"
        bucket.seq += 1

        t_rel_ms = int((now - bucket.t0_monotonic) * 1000)
        blurb = self._build_blurb(type_, phase, who, meta)
        payload: Dict[str, Any] = {
            "id": event_id,
            "t_rel_ms": t_rel_ms,
            "level": level,
            "phase": phase,
            "type": type_,
            "who": who,
            "blurb": blurb,
            "meta": meta,
            "schema": FLOW_SCHEMA_VERSION,
        }
        src, missing = _resolve_event_source(who, meta)
        payload["src"] = src
        if missing:
            payload["missing_source"] = True
        if parent_id:
            payload["parent_id"] = parent_id

        record = EventRecord(data=payload, monotonic_ts=now)
        self._append_event(bucket, record, record_drop=not is_injection)
        if parent_id:
            parent = bucket.event_index.get(parent_id)
            if parent:
                parent.children_ids.append(event_id)
        if not is_injection:
            turn_id = self._extract_turn_id(meta)
            key = (type_, who, turn_id)
            bucket.dedupe[key] = (now, event_id)
        self._event_sessions[event_id] = bucket.session_id
        if not is_injection:
            self._flush_drop_notices(bucket)
        return record

    def _append_event(
        self, bucket: SessionBucket, record: EventRecord, *, record_drop: bool = True
    ) -> None:
        if len(bucket.events) >= MAX_EVENTS:
            old = bucket.events.popleft()
            bucket.event_index.pop(old.data["id"], None)
            if record_drop:
                self._record_drop_notice(
                    bucket,
                    reason="event_limit",
                    meta={
                        "event_id": old.data.get("id"),
                        "event_type": old.data.get("type"),
                    },
                )
            self._on_event_removed(bucket, old)
        bucket.events.append(record)
        bucket.event_index[record.data["id"]] = record

    def _record_drop_notice(
        self, bucket: SessionBucket, *, reason: str, meta: Optional[Dict[str, Any]] = None
    ) -> None:
        payload: Dict[str, Any] = {"reason": reason, "count": 1}
        if meta:
            payload.update(meta)
        bucket.drop_buffer.append(payload)

    def _flush_drop_notices(self, bucket: SessionBucket) -> None:
        if not bucket.drop_buffer:
            return
        notices = bucket.drop_buffer
        bucket.drop_buffer = []
        aggregated: Dict[str, Dict[str, Any]] = {}
        for notice in notices:
            reason = str(notice.get("reason") or "unknown")
            entry = aggregated.setdefault(reason, {"count": 0})
            entry["count"] += int(notice.get("count") or 1)
            if "event_id" in notice:
                entry["last_event_id"] = notice["event_id"]
            if "event_type" in notice:
                entry.setdefault("event_types", set()).add(str(notice["event_type"]))
            if "bytes" in notice:
                entry["bytes"] = max(entry.get("bytes", 0), int(notice["bytes"]))
            if "parent_id" in notice:
                entry.setdefault("parent_ids", set()).add(str(notice["parent_id"]))
        now = time.monotonic()
        for reason, meta in aggregated.items():
            payload: Dict[str, Any] = {
                "__warning": FLOW_DROPPED_TYPE,
                "reason": reason,
                "count": meta.get("count", 1),
            }
            if "last_event_id" in meta:
                payload["last_event_id"] = meta["last_event_id"]
            if "bytes" in meta:
                payload["bytes"] = meta["bytes"]
            if "event_types" in meta:
                payload["event_types"] = sorted(meta["event_types"])
            if "parent_ids" in meta:
                payload["parent_ids"] = sorted(meta["parent_ids"])
            self._create_event(
                bucket,
                now,
                level="flow",
                phase="session",
                type_=FLOW_DROPPED_TYPE,
                who="system",
                meta=payload,
                parent_id=None,
                is_injection=True,
            )

    def _on_event_removed(self, bucket: SessionBucket, record: EventRecord) -> None:
        event_id = record.data["id"]
        meta = record.data.get("meta") or {}
        turn_id = self._extract_turn_id(meta)

        self._event_sessions.pop(event_id, None)

        confirm_key = self._confirm_key(meta)
        if confirm_key and bucket.open_confirms.get(confirm_key) == record:
            bucket.open_confirms.pop(confirm_key, None)
        key = turn_id or "__default__"
        if bucket.open_tts.get(key) == record:
            bucket.open_tts.pop(key, None)
        if bucket.open_llm.get(key) == record:
            bucket.open_llm.pop(key, None)
        state = bucket.tts_state.get(key)
        if state and state.get("event_id") == event_id:
            bucket.tts_state.pop(key, None)

    def _update_bucket_state(
        self, bucket: SessionBucket, record: EventRecord, turn_id: Optional[str]
    ) -> None:
        type_ = record.data["type"]
        meta = record.data.get("meta") or {}
        key = turn_id or "__default__"

        if type_ in BARGE_PAUSE_TYPES:
            bucket.barge_paused = True
        elif type_ == BARGE_RESUME_TYPE:
            bucket.barge_paused = False

        if type_ == ASR_PARTIAL_TYPE and turn_id:
            bucket.asr_partial_turns[turn_id] = record.data["id"]

        if type_ == CONFIRM_OPEN_TYPE:
            bucket.open_confirms[self._confirm_key(meta)] = record
        elif type_ == CONFIRM_CLOSE_TYPE:
            bucket.open_confirms.pop(self._confirm_key(meta), None)

        if type_ == TTS_START_TYPE:
            bucket.open_tts[key] = record
            bucket.tts_state[key] = {"active": True, "event_id": record.data["id"]}
        elif type_ == TTS_END_TYPE:
            bucket.open_tts.pop(key, None)
            state = bucket.tts_state.setdefault(key, {})
            state["active"] = False
            state["ended"] = True
            state["ended_event_id"] = record.data["id"]

        if type_ == LLM_START_TYPE:
            bucket.open_llm[key] = record
        elif type_ == LLM_FINAL_TYPE:
            bucket.open_llm.pop(key, None)

    def _confirm_key(self, meta: Dict[str, Any]) -> str:
        confirm_id = meta.get("confirm_id") if isinstance(meta, dict) else None
        if confirm_id is None:
            return "__default__"
        return str(confirm_id)

    def _inject_safety_events(self, bucket: SessionBucket) -> None:
        now = time.monotonic()
        # confirm close
        confirm_keys = list(bucket.open_confirms.keys())
        for key in confirm_keys:
            record = bucket.open_confirms.get(key)
            if not record:
                continue
            if (now - record.monotonic_ts) * 1000 <= 4000:
                continue
            meta = dict(record.data.get("meta") or {})
            meta.setdefault("reason", "timeout")
            meta.setdefault("__warning", "inferred_close")
            injected = self._create_event(
                bucket,
                now,
                level=record.data["level"],
                phase=record.data["phase"],
                type_=CONFIRM_CLOSE_TYPE,
                who="system",
                meta=meta,
                parent_id=record.data.get("parent_id"),
                is_injection=True,
            )
            bucket.open_confirms.pop(key, None)
            self._update_bucket_state(bucket, injected, self._extract_turn_id(meta))

        # tts end
        tts_keys = list(bucket.open_tts.keys())
        for key in tts_keys:
            record = bucket.open_tts.get(key)
            if not record:
                continue
            if now - record.monotonic_ts <= 120:
                continue
            meta = dict(record.data.get("meta") or {})
            meta.setdefault("__warning", "forced_close")
            injected = self._create_event(
                bucket,
                now,
                level=record.data["level"],
                phase=record.data["phase"],
                type_=TTS_END_TYPE,
                who=record.data.get("who", "system"),
                meta=meta,
                parent_id=record.data.get("parent_id"),
                is_injection=True,
            )
            bucket.open_tts.pop(key, None)
            self._update_bucket_state(bucket, injected, self._extract_turn_id(meta))

        # llm final
        llm_keys = list(bucket.open_llm.keys())
        for key in llm_keys:
            record = bucket.open_llm.get(key)
            if not record:
                continue
            if now - record.monotonic_ts <= 120:
                continue
            meta = dict(record.data.get("meta") or {})
            meta.setdefault("__warning", "forced_close")
            injected = self._create_event(
                bucket,
                now,
                level=record.data["level"],
                phase=record.data["phase"],
                type_=LLM_FINAL_TYPE,
                who=record.data.get("who", "system"),
                meta=meta,
                parent_id=record.data.get("parent_id"),
                is_injection=True,
            )
            bucket.open_llm.pop(key, None)
            self._update_bucket_state(bucket, injected, self._extract_turn_id(meta))

    def _parse_expand(self, expand: str) -> Dict[str, Any]:
        expand = expand or "none"
        expand = expand.strip()
        if expand == "all":
            return {"mode": "all", "ids": set()}
        if expand == "flow":
            return {"mode": "flow", "ids": set()}
        if expand == "none":
            return {"mode": "none", "ids": set()}
        if expand.startswith("ids:"):
            ids = {part.strip() for part in expand[4:].split(",") if part.strip()}
            return {"mode": "ids", "ids": ids}
        return {"mode": "none", "ids": set()}

    def _format_event(
        self,
        bucket: SessionBucket,
        record: EventRecord,
        expand_policy: Dict[str, Any],
        visited: Optional[Set[str]] = None,
    ) -> Dict[str, Any]:
        visited = visited or set()
        event_id = record.data["id"]
        if event_id in visited:
            return record.to_dict()
        visited.add(event_id)

        should_expand = self._should_expand_children(record, expand_policy)
        if not should_expand or not record.children_ids:
            return record.to_dict()

        child_dicts: List[Dict[str, Any]] = []
        for child_id in record.children_ids:
            child = bucket.event_index.get(child_id)
            if not child:
                continue
            child_dicts.append(self._format_event(bucket, child, expand_policy, visited))
        return record.to_dict(children=child_dicts)

    def _should_expand_children(
        self, record: EventRecord, expand_policy: Dict[str, Any]
    ) -> bool:
        mode = expand_policy.get("mode")
        if mode == "all":
            return True
        if mode == "none":
            return False
        if mode == "flow":
            return record.data.get("level") == "flow"
        if mode == "ids":
            return record.data.get("id") in expand_policy.get("ids", set())
        return False

    def _build_blurb(
        self, type_: str, phase: str, who: str, meta: Dict[str, Any]
    ) -> str:
        template = BLURBS.get(type_)
        context = {
            "turn": self._extract_turn_id(meta) or "",
            "phase": phase,
            "src": who,
        }
        if template:
            blurb = template.format(**context)
        else:
            blurb = type_.replace("_", " ").strip() or "event"
        words = _words(blurb)
        if len(words) <= 12:
            return " ".join(words)
        return " ".join(words[:12])

    def _compute_hints(self, bucket: SessionBucket) -> List[Dict[str, Any]]:
        records = list(bucket.events)
        if not records:
            return []

        event_index = bucket.event_index
        hints: List[Dict[str, Any]] = []
        seen_keys: Set[Tuple[str, Tuple[str, ...]]] = set()

        def _normalize_id(value: Optional[str]) -> Optional[str]:
            if not value:
                return None
            return str(value)

        def _numeric(value: Any) -> Optional[float]:
            if value is None:
                return None
            if isinstance(value, (int, float)):
                return float(value)
            try:
                return float(str(value))
            except (TypeError, ValueError):
                return None

        def _as_int(value: Any) -> Optional[int]:
            number = _numeric(value)
            if number is None:
                return None
            return int(number)

        def _add_hint(kind: str, severity: str, text: str, anchors: Iterable[str]) -> None:
            anchor_list = [anchor for anchor in (_normalize_id(a) for a in anchors) if anchor]
            if not anchor_list:
                return
            key = (kind, tuple(sorted(anchor_list)))
            if key in seen_keys:
                return
            seen_keys.add(key)
            hint_id = f"{kind}:{'-'.join(sorted(anchor_list))}"
            hints.append(
                {
                    "id": hint_id,
                    "severity": severity,
                    "text": text,
                    "anchors": anchor_list,
                }
            )

        last_tts_end: Optional[EventRecord] = None
        last_asr_error: Optional[EventRecord] = None

        for idx, record in enumerate(records):
            data = record.data
            type_ = data.get("type") or ""
            meta = data.get("meta") or {}

            if type_ == CONFIRM_OPEN_TYPE:
                start_ms = data.get("t_rel_ms") or 0
                confirm_id = data.get("id")
                found_within = False
                j = idx + 1
                while j < len(records):
                    next_record = records[j]
                    next_type = next_record.data.get("type") or ""
                    next_ms = next_record.data.get("t_rel_ms") or 0
                    delta = next_ms - start_ms
                    if next_type in {CONFIRM_OPEN_TYPE, CONFIRM_CLOSE_TYPE}:
                        break
                    if delta > 2000:
                        break
                    if next_type == ASR_PARTIAL_TYPE:
                        if delta <= 1200:
                            found_within = True
                        break
                    j += 1
                if not found_within:
                    _add_hint(
                        "no_asr_after_ready",
                        "warn",
                        "No ASR partial within 1200ms after confirm_open.",
                        [confirm_id],
                    )

                # evidence gate tracking
                vad_event: Optional[EventRecord] = None
                evidence_met = False
                j = idx + 1
                while j < len(records):
                    next_record = records[j]
                    next_type = next_record.data.get("type") or ""
                    if next_type == CONFIRM_OPEN_TYPE:
                        break
                    if next_type == CONFIRM_CLOSE_TYPE:
                        break
                    if next_type == "vad_gate_open" and vad_event is None:
                        vad_event = next_record
                    if next_type == "evidence_gate_met" and vad_event is not None:
                        evidence_met = True
                        break
                    j += 1
                if vad_event is not None and not evidence_met:
                    _add_hint(
                        "evidence_never_met",
                        "warn",
                        "Dual evidence not met (VAD open but no confident partial).",
                        [confirm_id, vad_event.data.get("id")],
                    )

            if type_ == "gate_check":
                rule = str(meta.get("rule") or "").lower()
                if rule == "min_tokens":
                    passed_val = meta.get("passed")
                    passed = bool(passed_val) if isinstance(passed_val, bool) else str(passed_val).lower() in {"1", "true", "yes"}
                    if not passed:
                        parent_id = data.get("parent_id") or data.get("id")
                        _add_hint(
                            "commit_blocked_min_tokens",
                            "warn",
                            "Gate rejected on min_tokens.",
                            [parent_id],
                        )

            if type_ == "tts_end":
                last_tts_end = record

            if type_ == "vad_gate_open":
                reason = str(meta.get("reason") or "").lower()
                if "mask" in reason and last_tts_end is not None:
                    dt = (data.get("t_rel_ms") or 0) - (last_tts_end.data.get("t_rel_ms") or 0)
                    if dt <= 3000:
                        _add_hint(
                            "post_tts_hold_overlap",
                            "warn",
                            "User speech during post-TTS hold; early audio masked.",
                            [last_tts_end.data.get("id"), data.get("id")],
                        )

            if type_ == "tts_metrics":
                first_byte = _numeric(meta.get("first_byte_ms"))
                if first_byte is not None and first_byte > 600:
                    parent_id = data.get("parent_id") or data.get("id")
                    _add_hint(
                        "tts_slow",
                        "warn",
                        "TTS first-byte latency above 600ms.",
                        [parent_id],
                    )

            if type_ == "queue_depth":
                depth = _as_int(meta.get("depth"))
                watermark = _as_int(meta.get("watermark"))
                threshold = watermark if watermark is not None else 8
                if depth is not None and depth >= threshold:
                    _add_hint(
                        "queue_pressure",
                        "warn",
                        "Mic or TTS queue hit high-water; possible backpressure.",
                        [data.get("id")],
                    )

            if type_ == "state_snapshot":
                queue_val = _as_int(meta.get("queue"))
                if queue_val is not None and queue_val >= 8:
                    anchor = data.get("parent_id") or data.get("id")
                    _add_hint(
                        "queue_pressure",
                        "warn",
                        "Mic or TTS queue hit high-water; possible backpressure.",
                        [anchor],
                    )

            if type_ == "asr_error":
                last_asr_error = record
            elif type_ == "recover_ok" and last_asr_error is not None:
                _add_hint(
                    "asr_recovered",
                    "info",
                    "ASR error then recovery succeeded.",
                    [last_asr_error.data.get("id"), data.get("id")],
                )
                last_asr_error = None

        def _event_time(event_id: str) -> int:
            record = event_index.get(event_id)
            if not record:
                return 0
            value = record.data.get("t_rel_ms")
            return int(value) if isinstance(value, int) else int(value or 0)

        hints.sort(key=lambda hint: min(_event_time(anchor) for anchor in hint["anchors"]))
        return hints


def _normalize_session_id(value: Any) -> str:
    try:
        text = str(value).strip()
    except Exception:
        return ""
    return text


def _coerce_json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        sanitized: Dict[str, Any] = {}
        for key, child in value.items():
            try:
                key_str = str(key)
            except Exception:
                key_str = repr(key)
            sanitized[key_str] = _coerce_json_value(child)
        return sanitized
    if isinstance(value, (list, tuple, set)):
        return [_coerce_json_value(item) for item in value]
    return str(value)


def _strip_none(mapping: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in mapping.items() if value is not None}


def _gzip_ndjson(records: Iterable[Dict[str, Any]]) -> Optional[bytes]:
    lines: List[str] = []
    for record in records:
        sanitized = _coerce_json_value(record)
        if isinstance(sanitized, dict):
            payload = _strip_none(sanitized)
        else:
            payload = {"value": sanitized}
        if not payload:
            continue
        try:
            line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        except TypeError:
            line = json.dumps(_coerce_json_value(payload), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        lines.append(line)
    if not lines:
        return None
    text = "\n".join(lines) + "\n"
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb") as gz_handle:
        gz_handle.write(text.encode("utf-8"))
    return buffer.getvalue()


def assemble_ws_frames(session_id: str) -> Optional[bytes]:
    store = FlowStore()
    sid = _normalize_session_id(session_id)
    if not sid:
        return None

    with store._lock:
        bucket = store._sessions.get(sid)
        if not bucket:
            return None
        events = [copy.deepcopy(record.data) for record in bucket.events]

    frame_entries: List[Dict[str, Any]] = []
    for event in events:
        type_value = str(event.get("type") or "")
        if type_value not in {"ws_frame_in", "ws_frame_out"}:
            continue
        meta = event.get("meta") if isinstance(event.get("meta"), Mapping) else {}
        entry: Dict[str, Any] = {
            "id": event.get("id"),
            "t_rel_ms": event.get("t_rel_ms"),
            "level": event.get("level"),
            "phase": event.get("phase"),
            "who": event.get("who"),
            "type": type_value,
            "direction": "out" if type_value == "ws_frame_out" else "in",
        }
        if meta:
            entry["meta"] = _coerce_json_value(dict(meta))
            route = meta.get("route")
            if route is not None:
                entry.setdefault("route", route)
            opcode = meta.get("type")
            if opcode is not None:
                entry.setdefault("opcode", opcode)
            bytes_len = meta.get("bytes")
            if bytes_len is not None:
                entry.setdefault("bytes", bytes_len)
            dropped = meta.get("dropped")
            if dropped is not None:
                entry.setdefault("dropped", dropped)
        frame_entries.append(_strip_none(entry))

    return _gzip_ndjson(frame_entries)


def slice_client_console_for_session(session_id: str) -> Optional[bytes]:
    store = FlowStore()
    sid = _normalize_session_id(session_id)
    if not sid:
        return None

    with store._lock:
        bucket = store._sessions.get(sid)
        if not bucket:
            return None
        events = [copy.deepcopy(record.data) for record in bucket.events]

    console_entries: List[Dict[str, Any]] = []
    for event in events:
        type_value = str(event.get("type") or "")
        if not type_value.startswith("client_"):
            continue
        meta = event.get("meta") if isinstance(event.get("meta"), Mapping) else {}
        entry: Dict[str, Any] = {
            "id": event.get("id"),
            "t_rel_ms": event.get("t_rel_ms"),
            "level": event.get("level"),
            "phase": event.get("phase"),
            "who": event.get("who"),
            "type": type_value,
        }
        if meta:
            entry["meta"] = _coerce_json_value(dict(meta))
        console_entries.append(_strip_none(entry))

    return _gzip_ndjson(console_entries)


def slice_server_log_for_session(session_id: str) -> Optional[bytes]:
    sid = _normalize_session_id(session_id)
    if not sid:
        return None

    try:
        history = get_admin_log_history()
    except Exception:
        history = []

    log_entries: List[Dict[str, Any]] = []
    for item in history:
        if not isinstance(item, Mapping):
            continue
        item_sid = _normalize_session_id(item.get("session_id") or item.get("sid"))
        if not item_sid or item_sid != sid:
            continue
        payload = _coerce_json_value(dict(item)) if isinstance(item, dict) else _coerce_json_value(item)
        if isinstance(payload, dict):
            payload["session_id"] = sid
            payload["sid"] = sid
            log_entries.append(_strip_none(payload))

    return _gzip_ndjson(log_entries)


__all__ = [
    "FlowStore",
    "assemble_ws_frames",
    "slice_client_console_for_session",
    "slice_server_log_for_session",
]
