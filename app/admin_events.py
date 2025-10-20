import threading
from queue import Queue

from app.obs.source_tags import FLOW_SCHEMA_VERSION
class AdminEvents:
    def __init__(self):
        self._subs = []
        self._lock = threading.Lock()
    def subscribe(self):
        q = Queue()
        with self._lock:
            self._subs.append(q)
        return q
    def emit(self, event, data):
        payload = data
        if isinstance(data, dict):
            # Avoid mutating the caller's payload when we need to inject schema data.
            payload = dict(data)
            payload.setdefault("schema", FLOW_SCHEMA_VERSION)
        for q in list(self._subs):
            q.put({"event": event, "data": payload})
admin_events = AdminEvents()
