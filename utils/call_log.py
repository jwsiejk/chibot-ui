# utils/call_log.py
from collections import deque
from datetime import datetime, timezone
import queue
import threading

class CallLog:
    def __init__(self, maxlen=500):
        self.entries = deque(maxlen=maxlen)
        self.listeners = set()
        self.lock = threading.Lock()

    def add(self, kind: str, msg: str, **extra):
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "msg": msg,
            **extra,
        }
        with self.lock:
            self.entries.appendleft(entry)
            for q in list(self.listeners):
                try:
                    q.put_nowait(entry)
                except Exception:
                    pass
        return entry

    def snapshot(self, n=200):
        with self.lock:
            return list(self.entries)[:n]

    def subscribe(self):
        q = queue.Queue()
        with self.lock:
            self.listeners.add(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.listeners.discard(q)

call_log = CallLog()
