
import asyncio, time, threading
from collections import deque
from typing import Deque, Dict, Optional

from .deepgram_client import FakeDeepgramClient
from ...ws.bus import emit_to_session

class StreamSession:
    def __init__(self, session_id: str, provider):
        self.session_id = session_id
        self.provider = provider
        self.queue: Deque[bytes] = deque(maxlen=32)
        self.last_enqueue = time.time()
        self.closed = False

    def put(self, data: bytes):
        self.queue.append(data)
        self.last_enqueue = time.time()

class StreamManager:
    def __init__(self):
        self.sessions: Dict[str, StreamSession] = {}
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def _get_provider(self):
        # Use fake client for now (mocked). Swap to real Deepgram in Phase 4–5 if desired.
        return FakeDeepgramClient({})

    def enqueue(self, session_id: str, data: bytes):
        sess = self.sessions.get(session_id)
        if not sess:
            provider = self._get_provider()
            sess = self.sessions[session_id] = StreamSession(session_id, provider)
            asyncio.run_coroutine_threadsafe(self._run_session(sess), self.loop)
        sess.put(data)

    async def _run_session(self, sess: StreamSession):
        await sess.provider.connect()
        IDLE = 9.0
        try:
            while not sess.closed:
                if (time.time() - sess.last_enqueue) > IDLE and not sess.queue:
                    await sess.provider.close()
                    sess.closed = True
                    break
                if sess.queue:
                    data = sess.queue.popleft()
                    await sess.provider.send(data)
                    # Fake emitter to simulate partials/final
                    c = getattr(sess.provider, "_count", 0)
                    if c in (2, 4):
                        await emit_to_session(sess.session_id, {"type":"user_partial","text":f"hello {c}"})
                    if c == 6:
                        await emit_to_session(sess.session_id, {"type":"user_final","text":"final hello"})
                await asyncio.sleep(0.01)
        finally:
            try:
                await sess.provider.close()
            except Exception:
                pass

_MANAGER: Optional[StreamManager] = None

def get_manager() -> StreamManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = StreamManager()
    return _MANAGER
