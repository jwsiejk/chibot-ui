import threading, time
from queue import Queue
try:
    from app.api_v1.admin import _emit
except Exception:
    def _emit(*a, **k):
        pass


class StreamBus:
    def __init__(self):
        self._subs = {}          # sid -> [Queue]
        self._locks = {}         # sid -> Lock
        self._canceled = set()   # (sid, tid)
        self._pending = {}       # sid -> [frame]
        self._max_pending = 256  # cap to avoid unbounded memory

    def _lock(self, sid):
        if sid not in self._locks:
            self._locks[sid] = threading.Lock()
        return self._locks[sid]

    def subscribe(self, sid):
        q = Queue()
        with self._lock(sid):
            self._subs.setdefault(sid, []).append(q)
            pend = self._pending.pop(sid, [])
        for fr in pend:
            try:
                t = fr.get('type')
                if t in {'text','audio_chunk','end','assistant_end','suggestions'}:
                    tid = fr.get('turn_id')
                    if tid and (sid, tid) in self._canceled:
                        continue
                q.put(fr)
            except Exception:
                pass
        return q

    def broadcast(self, sid, frame: dict):
        t = frame.get('type')
        if t in {'text','audio_chunk','end','assistant_end','suggestions'}:
            tid = frame.get('turn_id')
            if tid and (sid, tid) in self._canceled:
                return
        with self._lock(sid):
            subs = list(self._subs.get(sid, []))
            if not subs:
                buf = self._pending.setdefault(sid, [])
                # Backpressure: coalesce adjacent audio_chunk for same turn
                if frame.get('type') == 'audio_chunk' and buf and buf[-1].get('type') == 'audio_chunk' and buf[-1].get('turn_id') == frame.get('turn_id'):
                    try:
                        prev = buf[-1]
                        a = prev.get('base64') or ''
                        b = frame.get('base64') or ''
                        prev['base64'] = a + b
                        if len(prev['base64']) > 32768*6:
                            prev_data = prev['base64'][:32768*6]
                            rem_data = prev['base64'][32768*6:]
                            prev['base64'] = prev_data
                            buf.append({'type':'audio_chunk','turn_id': frame.get('turn_id'),'base64': rem_data})
                    except Exception:
                        buf.append(frame)
                else:
                    buf.append(frame)
                if len(buf) > self._max_pending:
                    drop_n = len(buf) - self._max_pending
                    del buf[:drop_n]
                    try:
                        _emit('backpressure_drop', sid=sid, dropped=drop_n, size=len(buf))
                    except Exception:
                        pass
                return
        for q in subs:
            try:
                q.put(frame)
            except Exception:
                pass

    def cancel_turn(self, sid, tid):
        if tid:
            self._canceled.add((sid, tid))
        with self._lock(sid):
            buf = self._pending.get(sid)
            if buf:
                self._pending[sid] = [
                    fr for fr in buf
                    if not (fr.get('turn_id') == tid and fr.get('type') in {'text','audio_chunk','end','assistant_end','suggestions'})
                ]

bus = StreamBus()
