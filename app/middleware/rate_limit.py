
import time, threading, os
from functools import wraps
from flask import request, jsonify

_WINDOW = float(os.environ.get("RATE_LIMIT_WINDOW_S", "1"))
_MAX = int(os.environ.get("RATE_LIMIT_MAX", "3"))
_MAX_VOICE = int(os.environ.get("RATE_LIMIT_MAX_VOICE_CHUNK", "16"))
_LOCK = threading.Lock()
from ..db import db
_BUCKETS = db.memory.setdefault('rl_buckets', {})

def _now(): return time.time()

def _key(name: str):
    ip = request.headers.get("X-Forwarded-For") or request.remote_addr or "unknown"
    return f"{ip}:{name}"

def _allowed_for(name: str) -> int:
    return _MAX_VOICE if name == 'voice_chunk' else _MAX

def register_before_request(app, paths=("/api/v1/chat", "/api/v1/voice/stt", "/ws/v1/chat")):
    @app.before_request
    def _rate_limit_guard():
        path = request.path or ""
        if path in paths:
            k = _key(path)
            t = _now()
            with _LOCK:
                arr = _BUCKETS.get(k, [])
                arr = [x for x in arr if x > t - _WINDOW]
                max_allowed = _allowed_for('voice_chunk' if path.endswith('/voice/chunk') else path)
                if len(arr) >= max_allowed:
                    return jsonify({"ok": False, "error": "rate_limited"}), 429
                arr.append(t)
                _BUCKETS[k] = arr
            return None
        return None

def limit(name: str):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **k):
            rv = check_now(name)
            if rv is not None:
                return rv
            return fn(*a, **k)
        return wrapper
    return deco

def check_now(name: str):
    k = _key(name)
    t = _now()
    with _LOCK:
        arr = _BUCKETS.get(k, [])
        arr = [x for x in arr if x > t - _WINDOW]
        max_allowed = _allowed_for(name)
        if len(arr) >= max_allowed:
            return jsonify({"ok": False, "error": "rate_limited"}), 429
        arr.append(t)
        _BUCKETS[k] = arr
    return None

def bucket_key(ip: str, session_id: str) -> str:
    return f"{ip}:{session_id or ''}"
