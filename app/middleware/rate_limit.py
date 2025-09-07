import time, threading, os
from functools import wraps
from flask import request, jsonify

_WINDOW = float(os.environ.get("RATE_LIMIT_WINDOW_S", "1"))
_MAX = int(os.environ.get("RATE_LIMIT_MAX", "3"))
_LOCK = threading.Lock()
# key -> [timestamps]
from ..db import db
_BUCKETS = db.memory.setdefault('rl_buckets', {})

def _now():
    return time.time()

def _key(name: str):
    ip = request.headers.get("X-Forwarded-For") or request.remote_addr or "unknown"
    return f"{name}:{ip}"

def limit(name: str, window_s: float=None, max_reqs: int=None):
    window = float(window_s or _WINDOW)
    maxn = int(max_reqs or _MAX)
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            k = _key(name)
            t = _now()
            with _LOCK:
                arr = _BUCKETS.get(k, [])
                # drop old
                arr = [x for x in arr if x > t - window]
                if len(arr) >= maxn:
                    return jsonify({"ok": False, "error":"rate_limited"}), 429
                arr.append(t)
                _BUCKETS[k] = arr
            return fn(*args, **kwargs)
        return wrapper
    return deco


def register_before_request(app, paths=("/api/v1/chat", "/api/v1/voice/stt")):
    @app.before_request
    def _rate_limit_guard():
        from flask import request
        path = request.path or ""
        if path in paths:
            k = _key(path)
            t = _now()
            with _LOCK:
                arr = _BUCKETS.get(k, [])
                arr = [x for x in arr if x > t - _WINDOW]
                if len(arr) >= _MAX:
                    return jsonify({"ok": False, "error":"rate_limited"}), 429
                arr.append(t)
                _BUCKETS[k] = arr
            # continue normally
            return None
        return None



def check_now(name: str):
    k = _key(name)
    t = _now()
    with _LOCK:
        arr = _BUCKETS.get(k, [])
        arr = [x for x in arr if x > t - _WINDOW]
        if len(arr) >= _MAX:
            return jsonify({"ok": False, "error":"rate_limited"}), 429
        arr.append(t)
        _BUCKETS[k] = arr
    return None


def bucket_key(ip: str, session_id: str) -> str:
    return f"{ip}:{session_id or ''}"
