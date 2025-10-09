import time, json
from typing import Dict, Any
from ..db import db

_listeners = []

def get_config() -> Dict[str, Any]:
    return db.get_config()


def get_config_value(key: str, default: Any = None) -> Any:
    return db.get_config(key=key, default=default)


def get_config_float(key: str, default: float) -> float:
    value = get_config_value(key, default)
    try:
        return float(value)
    except Exception:
        return float(default)


def get_planner_threshold_defaults() -> Dict[str, float]:
    return {
        "low": 0.0,
        "medium": get_config_float("planner_medium_threshold", 0.60),
        "high": get_config_float("planner_high_threshold", 0.75),
    }

def update_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    cfg = db.update_config(updates or {})
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


def get_config_version() -> int:
    try:
        import os
        if os.environ.get("DATABASE_URL"):
            from ..dal import neon_pg
            neon_pg.ensure_schema()
            # latest version id is ROWID in sqlite autoinc; return count
            db = neon_pg._connect(); cur = db.cursor()
            cur.execute("SELECT COALESCE(MAX(version),0) AS v FROM admin_settings")
            return int(cur.fetchone()["v"] or 0)
    except Exception:
        pass
    return 0
