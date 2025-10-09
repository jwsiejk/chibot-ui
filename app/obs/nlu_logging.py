from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

import time

from ..config import Settings, load_settings
from ..db import db
from ..security.redaction import redact_pii

try:
    from .. import obs as _obs
except ImportError:  # pragma: no cover - optional dependency
    _obs = None

_jlog = getattr(_obs, "jlog", None)
if _jlog is None:
    from . import jlog as _jlog


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    try:
        return bool(value)
    except Exception:
        return False


def _logging_enabled(settings: Settings, cfg: Dict[str, Any]) -> bool:
    env_toggle = getattr(settings, "enable_nlu_logging", None)
    if env_toggle is False:
        return False

    if "nlu_logging_enabled" not in cfg:
        return True

    value = cfg.get("nlu_logging_enabled")
    if value is None:
        return True

    return _coerce_bool(value)


def _normalize_alternates(raw: Any) -> List[Dict[str, Any]]:
    if not raw:
        return []
    if isinstance(raw, dict):
        raw = [raw]
    cleaned: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        intent = item.get("intent")
        if not intent:
            continue
        entry = {"intent": intent}
        if "confidence" in item:
            entry["confidence"] = item["confidence"]
        cleaned.append(entry)
        if len(cleaned) >= 2:
            break
    return cleaned


def _summarize_messages(messages: Optional[Sequence[Dict[str, Any]]]) -> Dict[str, Any]:
    if not messages:
        return {"message_count": 0}
    summary_roles: List[str] = []
    summary_lengths: List[int] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        summary_roles.append(str(msg.get("role") or ""))
        content = msg.get("content")
        try:
            summary_lengths.append(len(content or ""))
        except Exception:
            summary_lengths.append(0)
    out: Dict[str, Any] = {"message_count": len(messages)}
    if summary_roles:
        out["message_roles"] = summary_roles
    if summary_lengths:
        out["message_chars"] = summary_lengths
    return out


@dataclass
class NluLoggingContext:
    settings: Settings
    cfg: Dict[str, Any]
    turn_id: str
    session_id: Optional[str] = None
    correlation_id: Optional[str] = None
    correlation_user_msg_id: Optional[str] = None
    req_id: Optional[str] = None
    idem_key: Optional[str] = None
    source: Optional[str] = None
    channel: Optional[str] = None
    enabled: bool = field(init=False)
    _start_monotonic: Optional[float] = field(default=None, init=False)
    _done_emitted: bool = field(default=False, init=False)
    _fallback_fired: Optional[bool] = field(default=None, init=False)
    _fallback_reason: Optional[str] = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.enabled = _logging_enabled(self.settings, self.cfg)

    # --- helpers ---------------------------------------------------------
    def _base_fields(self) -> Dict[str, Any]:
        fields: Dict[str, Any] = {"turn_id": self.turn_id}
        if self.session_id:
            fields["session_id"] = self.session_id
        if self.correlation_id:
            fields["correlation_id"] = self.correlation_id
        if self.correlation_user_msg_id and self.correlation_user_msg_id != self.correlation_id:
            fields["correlation_user_msg_id"] = self.correlation_user_msg_id
        if self.req_id:
            fields["req_id"] = self.req_id
        if self.idem_key:
            fields["idem_key"] = self.idem_key
        if self.source:
            fields.setdefault("source", self.source)
        if self.channel:
            fields.setdefault("channel", self.channel)
        return fields

    def _emit(self, kind: str, **fields: Any) -> None:
        if not self.enabled:
            return
        payload = self._base_fields()
        for key, value in fields.items():
            if value is None:
                continue
            payload[key] = value
        _jlog(kind, **payload)

    # --- public API ------------------------------------------------------
    def log_start(self, text: str, *, meta: Optional[Dict[str, Any]] = None) -> None:
        if not self.enabled:
            return
        if meta:
            if not self.source and meta.get("source"):
                self.source = str(meta.get("source"))
            if not self.channel and meta.get("channel"):
                self.channel = str(meta.get("channel"))
        self._start_monotonic = time.monotonic()
        char_count = 0
        try:
            char_count = len(text or "")
        except Exception:
            char_count = 0
        self._emit("nlu.start", chars=char_count)

    def log_intent(self, labels: Dict[str, Any]) -> None:
        if not self.enabled or not isinstance(labels, dict):
            return
        intent = labels.get("intent")
        confidence = labels.get("confidence")
        alternates = _normalize_alternates(labels.get("alternates") or labels.get("intent_alternates"))
        self._emit("nlu.intent", intent=intent, confidence=confidence, alternates=alternates or None)

    def log_entities(self, entities: Optional[Dict[str, Any]]) -> None:
        if not self.enabled or not entities:
            return
        cleaned: Dict[str, Any] = {}
        for key, value in entities.items():
            try:
                cleaned[key] = redact_pii(value if isinstance(value, str) else str(value))
            except Exception:
                cleaned[key] = "[redacted]"
        if cleaned:
            self._emit("nlu.entities", entities=cleaned)

    def log_guardrail(self, *, decision: str = "allow", reason: Optional[str] = None) -> None:
        self._emit("nlu.guardrail", decision=decision, reason=reason)

    def log_teacher_move(
        self,
        *,
        resolved_move: Optional[str],
        policy_move: Optional[str],
        reason: Optional[str] = None,
    ) -> None:
        self._emit(
            "nlu.teacher_move",
            resolved_move=resolved_move,
            policy_move=policy_move,
            reason=reason,
        )

    def log_planner_decision(
        self,
        *,
        confidence: Optional[float],
        band: Optional[str],
        teacher_move: Optional[str],
        top_features: Optional[Sequence[str]] = None,
    ) -> None:
        if not self.enabled:
            return
        features = None
        if top_features:
            try:
                features = list(top_features)[:5]
            except Exception:
                features = [str(top_features)]
        self._emit(
            "planner.decision",
            confidence=confidence,
            band=band,
            teacher_move=teacher_move,
            features=features,
        )

    def log_toolplan(self, plan: Optional[Dict[str, Any]]) -> None:
        if not self.enabled:
            return
        tool = None
        payload = None
        if isinstance(plan, dict):
            tool = plan.get("tool")
            payload = plan.get("tool_payload")
            if isinstance(payload, dict):
                payload = {k: payload[k] for k in list(payload)[:5]}
            elif payload is not None:
                payload = str(payload)
        self._emit("nlu.toolplan", tool=tool, payload=payload)

    def log_prompt_summary(
        self,
        *,
        messages: Optional[Sequence[Dict[str, Any]]] = None,
        prompt_hash: Optional[str] = None,
        kb_count: int = 0,
    ) -> None:
        if not self.enabled:
            return
        summary = _summarize_messages(messages)
        summary["kb_count"] = int(kb_count)
        if prompt_hash:
            summary["prompt_hash"] = prompt_hash
        self._emit("nlu.prompt_summary", **summary)

    def log_llm_request(
        self,
        *,
        model: Optional[str],
        temperature: Optional[float],
        top_p: Optional[float],
        prompt_tokens: Optional[int],
        cache_status: Optional[str],
        tool_allowlist: Optional[Iterable[str]],
    ) -> None:
        allowlist = None
        if tool_allowlist is not None:
            try:
                allowlist = list(tool_allowlist)
            except Exception:
                allowlist = [str(tool_allowlist)]
        self._emit(
            "llm.request",
            model=model,
            temperature=temperature,
            top_p=top_p,
            prompt_tokens=prompt_tokens,
            cache_status=cache_status,
            tool_allowlist=allowlist,
        )

    def log_llm_response(
        self,
        *,
        output_tokens: Optional[int],
        finish_reason: Optional[str],
        preview: Optional[str],
        fallback_fired: bool,
        fallback_reason: Optional[str],
    ) -> None:
        self._emit(
            "llm.response",
            output_tokens=output_tokens,
            finish_reason=finish_reason,
            preview=preview,
            fallback=fallback_fired or None,
            fallback_reason=fallback_reason,
        )

    def mark_fallback(self, fired: bool, reason: Optional[str]) -> None:
        self._fallback_fired = fired
        self._fallback_reason = reason

    def log_error(self, error: str, message: Optional[str] = None) -> None:
        self._emit("nlu.error", error=error, message=message)

    def log_done(self) -> None:
        if not self.enabled or self._done_emitted:
            return
        end = time.monotonic()
        start = self._start_monotonic or end
        latency_ms = int((end - start) * 1000)
        self._done_emitted = True
        self._emit(
            "nlu.done",
            latency_ms=latency_ms,
            fallback=self._fallback_fired or None,
            fallback_reason=self._fallback_reason,
        )


_DEFAULT_SETTINGS = load_settings()


def create_context(
    *,
    turn_id: str,
    session_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    correlation_user_msg_id: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
    settings: Optional[Settings] = None,
    cfg: Optional[Dict[str, Any]] = None,
) -> NluLoggingContext:
    resolved_settings = settings or _DEFAULT_SETTINGS
    resolved_cfg = dict(cfg or db.get_config() or {})
    meta_dict = meta if isinstance(meta, dict) else {}
    source = meta_dict.get("source")
    channel = meta_dict.get("channel")
    req_id = meta_dict.get("req_id") or meta_dict.get("request_id")
    idem_key = meta_dict.get("idem_key") or meta_dict.get("idempotency_key")
    corr = (
        correlation_id
        or meta_dict.get("correlation_id")
        or correlation_user_msg_id
        or meta_dict.get("correlation_user_msg_id")
    )
    context = NluLoggingContext(
        settings=resolved_settings,
        cfg=resolved_cfg,
        turn_id=str(turn_id),
        session_id=session_id,
        correlation_id=corr,
        correlation_user_msg_id=correlation_user_msg_id or meta_dict.get("correlation_user_msg_id"),
        req_id=req_id,
        idem_key=idem_key,
        source=str(source) if source else None,
        channel=str(channel) if channel else None,
    )
    return context
