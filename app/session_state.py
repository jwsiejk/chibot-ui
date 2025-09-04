import time
from dataclasses import dataclass, field
from typing import Dict

@dataclass
class SessionState:
    last_user_action_ts: float = field(default_factory=lambda: 0.0)
    nudges_sent: int = 0

_SESS: Dict[str, SessionState] = {}

def get(sid: str) -> SessionState:
    st = _SESS.get(sid)
    if not st:
        st = SessionState()
        _SESS[sid] = st
    return st

def mark_user_action(sid: str):
    get(sid).last_user_action_ts = time.time()

def can_nudge(sid: str, backoff_after: int) -> bool:
    return get(sid).nudges_sent < backoff_after

def mark_nudge(sid: str):
    get(sid).nudges_sent += 1
