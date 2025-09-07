# app/services/dedupe_store.py
import time, hashlib, threading
from typing import Optional, Tuple

_mem = {}
_mem_lock = threading.Lock()
_ttl_s = 180

def make_key(kind: str, session_id: str, text: str) -> str:
    h = hashlib.sha256(f"{kind}|{session_id}|{text}".encode("utf-8")).hexdigest()
    return f"{kind}:{session_id}:{h}"

def remember(key: str, value: bytes):
    now = time.time()
    with _mem_lock:
        _mem[key] = (now, value)

def get(key: str) -> Optional[bytes]:
    now = time.time()
    with _mem_lock:
        v = _mem.get(key)
        if not v: return None
        ts, val = v
        if now - ts > _ttl_s:
            try: del _mem[key]
            except Exception: pass
            return None
        return val

def get_or_lock(key: str) -> bool:
    """
    Returns True if this is the first caller (lock acquired),
    False if a recent identical call exists (duplicate).
    """
    now = time.time()
    with _mem_lock:
        ts_val = _mem.get(key)
        if ts_val and now - ts_val[0] <= _ttl_s:
            return False
        # mark presence with None to signal in-flight
        _mem[key] = (now, None)
        return True
