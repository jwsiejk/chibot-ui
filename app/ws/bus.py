import threading, time
from queue import Queue

try:
    from app.admin_log import emit as _emit
except Exception:
    def _emit(*a, **k):
        pass


class StreamBus:
    """
    Lightweight pub/sub for session-scoped assistant frames.

    Notes
    -----
    • Cancel-aware: frames for a canceled (sid, turn_id) are dropped for
      cancellable types.
    • Backpressure when there are no subscribers: pending buffers are bounded
      per session and will coalesce adjacent audio frames.
    • Frame families supported:
        - text / assistant_chunk (LLM text)
        - audio_chunk (legacy single base64) / assistant_audio (list of base64)
        - end / assistant_end
        - suggestions
        - state (never canceled)
    """
    # Types that should be suppressed after cancel_turn(...)
    _CANCELLABLE_TYPES = {
        'text', 'assistant_chunk',
        'audio_chunk', 'assistant_audio',
        'end', 'assistant_end',
        'suggestions',
    }

    # Coalescing limits for pending audio:
    _LEGACY_BASE64_SOFT_LIMIT = 32768 * 6   # ~192 KB worth of base64 (heuristic)
    _ASSIST_AUDIO_MAX_CHUNKS = 256          # cap chunks per assistant_audio frame

    def is_canceled(self, sid, tid):
        try:
            return (sid, tid) in self._canceled
        except Exception:
            return False

    def __init__(self):
        self._subs = {}          # sid -> [Queue]
        self._locks = {}         # sid -> Lock
        self._canceled = set()   # (sid, tid)
        self._pending = {}       # sid -> [frame]
        self._max_pending = 256  # cap to avoid unbounded memory
        self._assistant_turn = {}  # sid -> current assistant turn_id

    def _lock(self, sid):
        if sid not in self._locks:
            self._locks[sid] = threading.Lock()
        return self._locks[sid]

    def subscribe(self, sid):
        q = Queue()
        with self._lock(sid):
            self._subs.setdefault(sid, []).append(q)
            pend = self._pending.pop(sid, [])
        # Drain pending into the new subscriber, honoring cancel state
        for fr in pend:
            try:
                t = fr.get('type')
                if t in self._CANCELLABLE_TYPES:
                    tid = fr.get('turn_id')
                    if tid and (sid, tid) in self._canceled:
                        continue
                q.put(fr)
            except Exception:
                pass
        return q

    def _coalesce_legacy_audio_chunk(self, buf, frame):
        """
        Coalesce adjacent legacy 'audio_chunk' frames with same turn_id by
        concatenating base64 payload. Split when exceeding a soft limit.
        """
        try:
            if buf and buf[-1].get('type') == 'audio_chunk' and buf[-1].get('turn_id') == frame.get('turn_id'):
                prev = buf[-1]
                a = prev.get('base64') or ''
                b = frame.get('base64') or ''
                prev['base64'] = a + b
                if len(prev['base64']) > self._LEGACY_BASE64_SOFT_LIMIT:
                    prev_data = prev['base64'][:self._LEGACY_BASE64_SOFT_LIMIT]
                    rem_data = prev['base64'][self._LEGACY_BASE64_SOFT_LIMIT:]
                    prev['base64'] = prev_data
                    buf.append({'type': 'audio_chunk', 'turn_id': frame.get('turn_id'), 'base64': rem_data})
            else:
                buf.append(frame)
        except Exception:
            buf.append(frame)

    def _coalesce_assistant_audio(self, buf, frame):
        """
        Coalesce adjacent 'assistant_audio' frames for the same turn by extending
        the 'audio_chunks' list and splitting if we exceed the max chunk limit.
        """
        try:
            if not isinstance(frame.get('audio_chunks'), list):
                # Normalize to list if a single chunk was provided by mistake
                ch = frame.get('audio_chunks')
                frame['audio_chunks'] = [ch] if ch else []

            if buf and buf[-1].get('type') == 'assistant_audio' and buf[-1].get('turn_id') == frame.get('turn_id'):
                prev = buf[-1]
                prev_chunks = prev.get('audio_chunks') or []
                new_chunks = frame.get('audio_chunks') or []
                prev_chunks.extend(new_chunks)
                # Split if too many chunks accumulated
                if len(prev_chunks) > self._ASSIST_AUDIO_MAX_CHUNKS:
                    keep = prev_chunks[:self._ASSIST_AUDIO_MAX_CHUNKS]
                    spill = prev_chunks[self._ASSIST_AUDIO_MAX_CHUNKS:]
                    prev['audio_chunks'] = keep
                    if spill:
                        buf.append({'type': 'assistant_audio', 'turn_id': frame.get('turn_id'), 'audio_chunks': spill})
            else:
                buf.append(frame)
        except Exception:
            buf.append(frame)

    def broadcast(self, sid, frame: dict):
        t = frame.get('type')
        if t in self._CANCELLABLE_TYPES:
            tid = frame.get('turn_id')
            if tid and (sid, tid) in self._canceled:
                return

        with self._lock(sid):
            subs = list(self._subs.get(sid, []))
            if not subs:
                buf = self._pending.setdefault(sid, [])
                # Backpressure/coalescing for audio while no subscribers are present
                if t == 'audio_chunk':
                    self._coalesce_legacy_audio_chunk(buf, frame)
                elif t == 'assistant_audio':
                    self._coalesce_assistant_audio(buf, frame)
                else:
                    buf.append(frame)

                # Enforce pending buffer bound
                if len(buf) > self._max_pending:
                    drop_n = len(buf) - self._max_pending
                    del buf[:drop_n]
                    try:
                        _emit('backpressure_drop', sid=sid, dropped=drop_n, size=len(buf))
                    except Exception:
                        pass
                return

        # Push to all active subscribers
        for q in subs:
            try:
                q.put(frame)
            except Exception:
                pass

    def note_assistant_turn(self, sid, tid):
        """Record the assistant turn currently streaming to the session."""
        lock = self._lock(sid)
        with lock:
            if tid is None:
                self._assistant_turn.pop(sid, None)
            else:
                self._assistant_turn[sid] = tid

    def current_assistant_turn(self, sid):
        """Return the active assistant turn_id for the session, if any."""
        lock = self._locks.get(sid)
        if lock is None:
            return self._assistant_turn.get(sid)
        with lock:
            return self._assistant_turn.get(sid)

    def unsubscribe(self, sid, q):
        """Remove a subscriber queue when a WS connection terminates."""
        lock = self._locks.get(sid)
        if lock is None:
            return

        with lock:
            subs = self._subs.get(sid)
            if not subs:
                return
            try:
                subs.remove(q)
            except ValueError:
                return
            if not subs:
                self._subs.pop(sid, None)
                self._pending.pop(sid, None)
                self._locks.pop(sid, None)

    def cancel_turn(self, sid, tid):
        if tid:
            self._canceled.add((sid, tid))
        with self._lock(sid):
            buf = self._pending.get(sid)
            if buf:
                self._pending[sid] = [
                    fr for fr in buf
                    if not (
                        fr.get('turn_id') == tid and
                        (fr.get('type') in self._CANCELLABLE_TYPES)
                    )
                ]
        self.note_assistant_turn(sid, None)


bus = StreamBus()
