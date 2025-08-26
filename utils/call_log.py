# utils/call_log.py
from collections import deque
from datetime import datetime, timezone
import threading, queue, json

class CallLog:
    def __init__(self, maxlen: int = 1000):
        self._entries = deque(maxlen=maxlen)
        self._listeners = set()
        self._lock = threading.Lock()

    def add(self, kind: str, msg: str, **extra):
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": str(kind or ""),
            "msg": str(msg or ""),
        }
        # Copy only JSON-serializable extras
        for k, v in (extra or {}).items():
            try:
                json.dumps(v)
                entry[k] = v
            except Exception:
                entry[k] = str(v)
        with self._lock:
            self._entries.append(entry)
            for q in list(self._listeners):
                try:
                    q.put_nowait(entry)
                except Exception:
                    pass
        return entry

    def recent(self, n: int = 200):
        n = int(n or 200)
        with self._lock:
            # newest first
            return list(self._entries)[-n:][::-1]

    def clear(self):
        with self._lock:
            self._entries.clear()

    def subscribe(self):
        q = queue.Queue()
        with self._lock:
            self._listeners.add(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            self._listeners.discard(q)

call_log = CallLog()
