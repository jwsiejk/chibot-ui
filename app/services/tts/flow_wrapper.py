"""Flow trace helpers for TTS playback."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

from app.flow.emit import emit as flow_emit


@runtime_checkable
class _Tracker(Protocol):
    """Protocol describing the minimal TTS flow tracker."""

    def queue(self) -> None:
        ...

    def start(self) -> None:
        ...

    def end(self) -> None:
        ...


class _NullTracker:
    """No-op tracker when flow context is unavailable."""

    __slots__ = ()

    def queue(self) -> None:  # noqa: D401 - trivial
        """Ignore queue events."""
        return None

    def start(self) -> None:  # noqa: D401 - trivial
        """Ignore start events."""
        return None

    def end(self) -> None:  # noqa: D401 - trivial
        """Ignore end events."""
        return None


@dataclass
class TTSFlowTracker:
    """Emit ``tts_*`` flow events for a single synthesis request."""

    session_id: str
    phase: str
    who: str = "system"
    turn_id: Optional[str] = None
    include_turn_meta: bool = True

    _queued: bool = False
    _started: bool = False
    _ended: bool = False

    def _meta(self) -> Optional[dict[str, object]]:
        if not self.include_turn_meta or not self.turn_id:
            return None
        return {"turn_id": self.turn_id}

    def queue(self) -> None:
        if self._queued:
            return
        self._queued = True
        try:
            flow_emit(
                session_id=self.session_id,
                level="flow",
                phase=self.phase,
                type="tts_queue",
                who=self.who,
                meta=self._meta(),
            )
        except Exception:
            pass

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        try:
            flow_emit(
                session_id=self.session_id,
                level="flow",
                phase=self.phase,
                type="tts_start",
                who=self.who,
                meta=self._meta(),
            )
        except Exception:
            pass

    def end(self) -> None:
        if self._ended or not self._started:
            return
        self._ended = True
        try:
            flow_emit(
                session_id=self.session_id,
                level="flow",
                phase=self.phase,
                type="tts_end",
                who=self.who,
                meta=self._meta(),
            )
        except Exception:
            pass


def _normalize_turn_id(turn_id: Optional[object]) -> Optional[str]:
    if turn_id in (None, ""):
        return None
    try:
        tid = str(turn_id)
    except Exception:
        return None
    return tid or None


def make_tts_flow_tracker(
    session_id: Optional[object],
    *,
    phase: Optional[object],
    turn_id: Optional[object] = None,
    include_turn_id: bool = True,
) -> _Tracker:
    """Return a ``TTSFlowTracker`` when flow tracing is available."""

    try:
        session_str = str(session_id)
    except Exception:
        return _NullTracker()
    if not session_str:
        return _NullTracker()
    try:
        phase_str = str(phase or "").strip()
    except Exception:
        return _NullTracker()
    if not phase_str:
        return _NullTracker()
    normalized_turn = _normalize_turn_id(turn_id) if include_turn_id else _normalize_turn_id(None)
    return TTSFlowTracker(
        session_id=session_str,
        phase=phase_str,
        turn_id=normalized_turn,
        include_turn_meta=include_turn_id,
    )

