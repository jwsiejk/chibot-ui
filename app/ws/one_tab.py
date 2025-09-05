import threading

_active = set()
_lock = threading.Lock()

def acquire(key: str) -> bool:
    with _lock:
        if key in _active:
            return False
        _active.add(key)
        return True

def release(key: str):
    with _lock:
        if key in _active:
            _active.remove(key)