import time, json
from typing import Dict, Any
from ..db import db

_listeners = []

def get_config() -> Dict[str, Any]:
    return db.get_config()

def update_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    cfg = db.set_config(updates or {})
    # notify listeners
    payload = {"event":"config_updated","config": cfg, "updated_at": time.time()}
    for q in list(_listeners):
        q.append(json.dumps(payload))
    return cfg

def subscribe():
    q = []
    _listeners.append(q)
    return q

def unsubscribe(q):
    if q in _listeners:
        _listeners.remove(q)
