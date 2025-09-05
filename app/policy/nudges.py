import threading, time
from typing import Dict
from ..db import db
from ..ws.bus import bus
from ..services.streaming import make_assistant_frames

# Scheduled nudges by session_id
_scheduled: Dict[str, threading.Timer] = {}

def _nudge_text():
    return "Still with me? Want a quick recap?"

def _nudge_delay_ms():
    cfg = db.get_config()
    return int(cfg.get("nudge_delay_ms", 4200))

def _enabled():
    cfg = db.get_config()
    return bool(cfg.get("nudges_enabled", True))

def _backoff_max():
    cfg = db.get_config()
    return int(cfg.get("nudge_backoff_after_ignored", 2))

def arm_nudge(session_id: str):
    if not _enabled():
        return False
    # Check per-session backoff
    s = db.memory['sessions'].setdefault(session_id, {'email': 'user@example.com','messages': [], 'nudges': 0, 'persona_id':'chip'})
    if s.get('nudges', 0) >= _backoff_max():
        return False

    def fire():
        # If canceled/replaced, exit early
        if session_id not in _scheduled:
            return
        # Build and send a short nudge response
        tid, frames = make_assistant_frames(_nudge_text())
        for fr in frames:
            if fr.get("type") == "end":
                fr["reason"] = "nudge"
            bus.broadcast(session_id, fr)
        # Count this nudge
        s = db.memory['sessions'].setdefault(session_id, {'email': 'user@example.com','messages': [], 'nudges': 0, 'persona_id':'chip'})
        s['nudges'] = s.get('nudges', 0) + 1
        # Done; remove from scheduled
        _scheduled.pop(session_id, None)

    delay = max(0, _nudge_delay_ms()) / 1000.0
    t = threading.Timer(delay, fire)
    # Overwrite any existing
    cancel_nudge(session_id)
    _scheduled[session_id] = t
    t.daemon = True
    t.start()
    return True

def cancel_nudge(session_id: str):
    t = _scheduled.pop(session_id, None)
    if t:
        try:
            t.cancel()
        except Exception:
            pass

def _cancel_all():
    for sid in list(_scheduled.keys()):
        cancel_nudge(sid)