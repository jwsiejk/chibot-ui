from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque, Dict, Iterable, List, Optional, Set, Tuple

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


class FlowStore:
    """Thread-safe, singleton flow trace store."""

    _instance: Optional["FlowStore"] = None
    _singleton_lock = threading.Lock()

    def __new__(cls) -> "FlowStore":
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init()
        return cls._instance

    def _init(self) -> None:
        self._lock = threading.RLock()
        self._sessions: Dict[str, SessionBucket] = {}

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
            raise ValueError("batch too large")
        serialized = str(items_list).encode("utf-8")
        if len(serialized) > MAX_BATCH_BYTES:
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

            return {
                "session_id": session_id,
                "started_at": bucket.started_at_iso,
                "events": events,
                "next_since_ms": next_since,
            }

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
        }
        if parent_id:
            payload["parent_id"] = parent_id

        record = EventRecord(data=payload, monotonic_ts=now)
        self._append_event(bucket, record)
        if parent_id:
            parent = bucket.event_index.get(parent_id)
            if parent:
                parent.children_ids.append(event_id)
        if not is_injection:
            turn_id = self._extract_turn_id(meta)
            key = (type_, who, turn_id)
            bucket.dedupe[key] = (now, event_id)
        return record

    def _append_event(self, bucket: SessionBucket, record: EventRecord) -> None:
        if len(bucket.events) >= MAX_EVENTS:
            old = bucket.events.popleft()
            bucket.event_index.pop(old.data["id"], None)
            self._on_event_removed(bucket, old)
        bucket.events.append(record)
        bucket.event_index[record.data["id"]] = record

    def _on_event_removed(self, bucket: SessionBucket, record: EventRecord) -> None:
        event_id = record.data["id"]
        meta = record.data.get("meta") or {}
        turn_id = self._extract_turn_id(meta)

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


__all__ = ["FlowStore"]
