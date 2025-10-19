"""Flow trace helpers for LLM interactions."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

from app.flow.emit import add_batch, emit as flow_emit


@runtime_checkable
class _Tracker(Protocol):
    def start(self) -> bool:
        ...

    def final(self, *, tokens_out: Optional[int] = None, text: Optional[str] = None, chars: Optional[int] = None) -> None:
        ...

    def error(self, code: Optional[object] = None) -> None:
        ...


class _NullTracker:
    """No-op tracker used when flow trace context is unavailable."""

    __slots__ = ()

    def queue(self) -> None:  # noqa: D401 - trivial
        """Ignore queue notifications when tracing is disabled."""
        return None

    def start(self) -> bool:  # noqa: D401 - trivial
        """Pretend to start a span and report no work."""
        return False

    def first_token(self) -> None:  # noqa: D401 - trivial
        """Ignore first-token hints when tracing is disabled."""
        return None

    def final(self, *, tokens_out: Optional[int] = None, text: Optional[str] = None, chars: Optional[int] = None) -> None:
        """Ignore completion for missing spans."""
        return None

    def error(self, code: Optional[object] = None) -> None:  # noqa: D401 - trivial
        """Ignore error notifications when tracing is disabled."""
        return None


def _coerce_int(value: Optional[object]) -> Optional[int]:
    """Best-effort conversion to int, returning ``None`` on failure."""

    if value is None:
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


@dataclass
class LLMFlowTracker:
    """Manage ``llm_start``/``llm_final`` flow events for a single turn."""

    session_id: str
    phase: str
    turn_id: Optional[str] = None
    model: Optional[str] = None
    who: str = "system"

    _started: bool = False
    _start_event_id: Optional[str] = None
    _start_monotonic: Optional[float] = None
    _queue_start_monotonic: Optional[float] = None
    _queue_end_monotonic: Optional[float] = None
    _first_token_monotonic: Optional[float] = None

    def queue(self) -> None:
        """Mark the time at which the request entered the provider queue."""

        self._queue_start_monotonic = time.monotonic()

    def start(self) -> bool:
        """Emit the ``llm_start`` event if possible."""

        if not self.session_id or not self.phase:
            return False
        meta: dict[str, object] = {}
        if self.turn_id:
            meta["turn_id"] = str(self.turn_id)
        if self.model:
            meta["model"] = str(self.model)
        try:
            event_id = flow_emit(
                session_id=self.session_id,
                level="flow",
                phase=self.phase,
                type="llm_start",
                who=self.who,
                meta=meta or None,
            )
        except Exception:
            return False
        self._started = True
        self._start_event_id = event_id or None
        now = time.monotonic()
        self._start_monotonic = now
        if self._queue_start_monotonic is None:
            self._queue_start_monotonic = now
        self._queue_end_monotonic = now
        return True

    def first_token(self) -> None:
        """Record when the first token was observed from the provider."""

        self._first_token_monotonic = time.monotonic()

    def final(
        self,
        *,
        tokens_out: Optional[int] = None,
        tokens_in: Optional[int] = None,
        finish_reason: Optional[object] = None,
        text: Optional[str] = None,
        chars: Optional[int] = None,
    ) -> None:
        """Emit the ``llm_final`` event when output text is ready."""

        if not self._started or not self.session_id or not self.phase:
            return
        meta: dict[str, object] = {}
        if self.turn_id:
            meta["turn_id"] = str(self.turn_id)
        prompt_tokens = _coerce_int(tokens_in)
        if prompt_tokens is not None and prompt_tokens >= 0:
            meta["tokens_in"] = prompt_tokens
        token_count = _coerce_int(tokens_out)
        if token_count is not None and token_count >= 0:
            meta["tokens_out"] = token_count
        else:
            char_count = _coerce_int(chars)
            if char_count is None:
                char_count = len(text or "")
            if char_count < 0:
                char_count = 0
            meta["chars"] = char_count
        try:
            flow_emit(
                session_id=self.session_id,
                level="flow",
                phase=self.phase,
                type="llm_final",
                who=self.who,
                meta=meta or None,
            )
            if self._start_event_id:
                stream_items = []
                now = time.monotonic()
                if self._start_monotonic:
                    dt_ms = int(max(0.0, (now - self._start_monotonic) * 1000))
                else:
                    dt_ms = 0
                bytes_len = 0
                if text:
                    try:
                        bytes_len = len((text or "").encode("utf-8"))
                    except Exception:
                        bytes_len = len(text or "")
                stream_items.append({"dt_ms": dt_ms, "bytes": bytes_len})
                debug_meta: dict[str, object] = {}
                queue_start = self._queue_start_monotonic or self._start_monotonic
                queue_end = self._queue_end_monotonic or self._start_monotonic
                if queue_start is not None and queue_end is not None:
                    queue_ms = int(max(0.0, (queue_end - queue_start) * 1000))
                    debug_meta["queue_ms"] = queue_ms
                base = queue_end or queue_start or self._start_monotonic
                if base is not None:
                    if self._first_token_monotonic is not None:
                        first_token_ms = int(
                            max(0.0, (self._first_token_monotonic - base) * 1000)
                        )
                        debug_meta["first_token_ms"] = first_token_ms
                    full_ms = int(max(0.0, (now - base) * 1000))
                    debug_meta["full_ms"] = full_ms
                if prompt_tokens is not None and prompt_tokens >= 0:
                    debug_meta["tokens_in"] = prompt_tokens
                if token_count is not None and token_count >= 0:
                    debug_meta["tokens_out"] = token_count
                if finish_reason is not None:
                    try:
                        reason_text = str(finish_reason)
                    except Exception:
                        reason_text = None
                    if reason_text:
                        debug_meta["finish_reason"] = reason_text
                if debug_meta:
                    try:
                        flow_emit(
                            session_id=self.session_id,
                            level="debug",
                            phase=self.phase,
                            type="llm_latency",
                            who=self.who,
                            meta=debug_meta,
                            parent_id=self._start_event_id,
                        )
                    except Exception:
                        pass
                try:
                    add_batch(self._start_event_id, "llm_stream", stream_items)
                except Exception:
                    pass
        except Exception:
            pass
        self._started = False
        self._start_event_id = None
        self._start_monotonic = None
        self._queue_start_monotonic = None
        self._queue_end_monotonic = None
        self._first_token_monotonic = None

    def error(
        self,
        code: Optional[object] = None,
        *,
        state: Optional[dict[str, object]] = None,
    ) -> None:
        """Emit an ``llm_error`` transition with the provided code."""

        if not self.session_id or not self.phase:
            return
        meta: dict[str, object] = {}
        if code is not None:
            try:
                meta["code"] = str(code)
            except Exception:
                meta["code"] = "unknown"
        try:
            event_id = flow_emit(
                session_id=self.session_id,
                level="transition",
                phase=self.phase,
                type="llm_error",
                who=self.who,
                meta=meta or None,
            )
            if state:
                cleaned = {
                    key: value
                    for key, value in state.items()
                    if value is not None
                }
                try:
                    flow_emit(
                        session_id=self.session_id,
                        level="debug",
                        phase=self.phase,
                        type="state_snapshot",
                        who=self.who,
                        meta=cleaned or None,
                        parent_id=event_id or None,
                    )
                except Exception:
                    pass
        except Exception:
            pass


def make_llm_flow_tracker(
    session_id: Optional[str],
    *,
    phase: str,
    turn_id: Optional[str] = None,
    model: Optional[str] = None,
) -> _Tracker:
    """Return an ``LLMFlowTracker`` when tracing is possible, else a no-op."""

    if not session_id or not phase:
        return _NullTracker()
    tracker = LLMFlowTracker(
        session_id=session_id,
        phase=phase,
        turn_id=turn_id,
        model=model,
    )
    return tracker
