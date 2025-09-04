import threading
from queue import Queue
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
        for q in list(self._subs):
            q.put({"event": event, "data": data})
admin_events = AdminEvents()
