import threading, time
from queue import Queue
class StreamBus:
    def __init__(self):
        self._subs = {}
        self._locks = {}
        self._canceled = set()
    def _lock(self, sid):
        import threading
        if sid not in self._locks: self._locks[sid]=threading.Lock()
        return self._locks[sid]
    def subscribe(self, sid):
        q = Queue()
        with self._lock(sid):
            self._subs.setdefault(sid, []).append(q)
        return q
    def broadcast(self, sid, frame: dict):
        t = frame.get('type')
        if t in {'text','audio_chunk','end','suggestions'}:
            tid = frame.get('turn_id')
            if tid and (sid, tid) in self._canceled: return
        for q in list(self._subs.get(sid, [])): q.put(frame)
    def cancel_turn(self, sid, tid):
        if tid: self._canceled.add((sid, tid))
bus = StreamBus()
