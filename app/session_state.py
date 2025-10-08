import time
from dataclasses import dataclass, field
from typing import Dict, Optional

@dataclass
class SessionState:
    last_user_activity_ts: float = field(default_factory=lambda: 0.0)
    last_partial_ts: float = field(default_factory=lambda: 0.0)
    last_utterance_end_ts: float = field(default_factory=lambda: 0.0)
    asr_stream_open: bool = False
    recorder_active: bool = False
    phase: str = ""
    nudges_sent: int = 0

_SESS: Dict[str, SessionState] = {}

def get(sid: str) -> SessionState:
    st = _SESS.get(sid)
    if not st:
        st = SessionState()
        _SESS[sid] = st
    return st

def mark_user_activity(sid: str, when: Optional[float] = None) -> None:
    ts = when if when is not None else time.time()
    st = get(sid)
    if ts > st.last_user_activity_ts:
        st.last_user_activity_ts = ts


def mark_user_action(sid: str):
    mark_user_activity(sid)

def can_nudge(sid: str, backoff_after: int) -> bool:
    return get(sid).nudges_sent < backoff_after

def mark_nudge(sid: str):
    get(sid).nudges_sent += 1


def note_partial(sid: str, when: Optional[float] = None) -> None:
    ts = when if when is not None else time.time()
    st = get(sid)
    st.last_partial_ts = ts
    if ts > st.last_user_activity_ts:
        st.last_user_activity_ts = ts


def note_utterance_end(sid: str, when: Optional[float] = None) -> None:
    ts = when if when is not None else time.time()
    st = get(sid)
    st.last_utterance_end_ts = ts
    if ts > st.last_user_activity_ts:
        st.last_user_activity_ts = ts


def set_asr_stream_open(sid: str, is_open: bool) -> None:
    st = get(sid)
    st.asr_stream_open = bool(is_open)


def set_recorder_active(sid: str, active: bool) -> None:
    st = get(sid)
    st.recorder_active = bool(active)


def set_phase(sid: str, phase: str) -> None:
    st = get(sid)
    st.phase = phase or ""


def last_user_activity_ts(sid: str) -> float:
    return get(sid).last_user_activity_ts


def idle_duration_ms(sid: str) -> float:
    ts = get(sid).last_user_activity_ts
    if ts <= 0:
        return float("inf")
    return max(0.0, (time.time() - ts) * 1000.0)


def silence_guard_satisfied(sid: str, guard_ms: int) -> bool:
    guard = max(0, int(guard_ms))
    if guard == 0:
        return True
    st = get(sid)
    if st.last_utterance_end_ts <= 0:
        return True
    ref = max(st.last_partial_ts, st.last_utterance_end_ts)
    if ref <= 0:
        return True
    return (time.time() - ref) * 1000.0 >= guard


def is_ready_for_nudge(sid: str, guard_ms: int) -> bool:
    st = get(sid)
    if st.phase and st.phase != "ready":
        return False
    if st.recorder_active:
        return False
    if st.asr_stream_open:
        return False
    return silence_guard_satisfied(sid, guard_ms)


def guard_remaining_ms(sid: str, guard_ms: int) -> float:
    guard = max(0, int(guard_ms))
    if guard == 0:
        return 0.0
    st = get(sid)
    if st.last_utterance_end_ts <= 0:
        return 0.0
    ref = max(st.last_partial_ts, st.last_utterance_end_ts)
    if ref <= 0:
        return 0.0
    elapsed = (time.time() - ref) * 1000.0
    remaining = guard - elapsed
    return max(0.0, remaining)
