"""Flow trace helpers for LLM interactions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

from app.flow.emit import emit as flow_emit


@runtime_checkable
class _Tracker(Protocol):
    def start(self) -> bool:
        ...

    def final(self, *, tokens_out: Optional[int] = None, text: Optional[str] = None, chars: Optional[int] = None) -> None:
        ...


class _NullTracker:
    """No-op tracker used when flow trace context is unavailable."""

    __slots__ = ()

    def start(self) -> bool:  # noqa: D401 - trivial
        """Pretend to start a span and report no work."""
        return False

    def final(self, *, tokens_out: Optional[int] = None, text: Optional[str] = None, chars: Optional[int] = None) -> None:
        """Ignore completion for missing spans."""
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
            flow_emit(
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
        return True

    def final(
        self,
        *,
        tokens_out: Optional[int] = None,
        text: Optional[str] = None,
        chars: Optional[int] = None,
    ) -> None:
        """Emit the ``llm_final`` event when output text is ready."""

        if not self._started or not self.session_id or not self.phase:
            return
        meta: dict[str, object] = {}
        if self.turn_id:
            meta["turn_id"] = str(self.turn_id)
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
        except Exception:
            pass
        self._started = False


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
