import time
from functools import wraps
from flask import request, jsonify

_buckets = {}

def rate_limit(calls:int, per_seconds:float, key_func=lambda: request.remote_addr or 'test'):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            key = (fn.__name__, key_func())
            now = time.time()
            window = per_seconds
            bucket = _buckets.get(key, {'ts': now, 'count': 0})
            # reset window if elapsed
            if now - bucket['ts'] > window:
                bucket = {'ts': now, 'count': 0}
            bucket['count'] += 1
            _buckets[key] = bucket
            if bucket['count'] > calls:
                return jsonify({"ok": False, "error": "rate_limited"}), 429
            return fn(*args, **kwargs)
        return wrapper
    return deco
