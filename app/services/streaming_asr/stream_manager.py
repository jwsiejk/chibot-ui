# app/services/streaming_asr/stream_manager.py
from __future__ import annotations

import asyncio
import time
import threading
from collections import deque
from typing import Deque, Dict, Optional

from app.ws.bus import bus
from app.api_v1.admin import _emit
from app.db import db
from app.services.streaming import make_assistant_frames, schedule_frames
from app.security_state import get_user                      # use your existing bus
from .deepgram_client import FakeDeepgramClient # swap to real client later

class StreamSession:
    def __init__(self, session_id: str, provider):
        self.session_id = session_id
        self.provider = provider
        self.queue: Deque[bytes] = deque(maxlen=32)  # backpressure window
        self.last_enqueue = time.time()
        self.closed = False

    def put(self, data: bytes) -> None:
        self.queue.append(data)
        self.last_enqueue = time.time()

class StreamManager:
    """
    Holds one streaming ASR client per user session.
    Enqueues Opus timeslices (from POST /api/v1/voice/stt/stream),
    feeds provider, and emits user_partial / user_final to WS bus.
    """
    def __init__(self) -> None:
        self.sessions: Dict[str, StreamSession] = {}
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def _get_provider(self):
        # Deterministic fake; wire real Deepgram via Admin config later.
        return FakeDeepgramClient({})

    def enqueue(self, session_id: str, data: bytes) -> None:
        sess = self.sessions.get(session_id)
        if not sess:
            provider = self._get_provider()
            sess = self.sessions[session_id] = StreamSession(session_id, provider)
            asyncio.run_coroutine_threadsafe(self._run_session(sess), self.loop)
        sess.put(data)

    async def _run_session(self, sess: StreamSession) -> None:
        await sess.provider.connect()
        IDLE_TIMEOUT = 9.0
        try:
            while not sess.closed:
                # idle close
                if (time.time() - sess.last_enqueue) > IDLE_TIMEOUT and not sess.queue:
                    await sess.provider.close()
                    sess.closed = True
                    break

                # feed provider
                if sess.queue:
                    data = sess.queue.popleft()
                    await sess.provider.send(data)

                    # Fake provider behavior: partials on counts 2/4, final on 6.
                    count = getattr(sess.provider, "_count", 0)
                    if count in (2, 4):
                        try:
                            _emit('user_partial', session_id=sess.session_id, count=count)
                        except Exception:
                            pass
                        bus.broadcast(sess.session_id, {
                            "type": "user_partial",
                            "text": f"hello {count}"
                        })
                    if count == 6:
                        final_text = "final hello"
                        try:
                            _emit('user_final', session_id=sess.session_id)
                        except Exception:
                            pass
                        bus.broadcast(sess.session_id, {
                            "type": "user_final",
                            "text": final_text
                        })
                        # Promote to user turn -> LLM turn
                        try:
                            email = get_user()
                        except Exception:
                            email = "user@example.com"
                        try:
                            db.ensure_session(sess.session_id, email)
                            if final_text:
                                db.add_message(sess.session_id, "user", final_text)
                            tid, frames = make_assistant_frames(final_text, session_id=sess.session_id, meta={})
                            schedule_frames(sess.session_id, frames, enable_nudge=True)
                        except Exception:
                            pass
                
                await asyncio.sleep(0.01)
        finally:
            try:
                await sess.provider.close()
            except Exception:
                pass

    # ---- graceful shutdown for deploy/worker SIGTERM ----
    def shutdown(self, join_timeout: float = 2.0) -> None:
        """
        Stop the background event loop and thread cleanly.
        Close any live provider connections before stopping the loop.
        Safe to call multiple times.
        """
        if not self.thread or not self.loop:
            return

        async def _close_all():
            for sess in list(self.sessions.values()):
                try:
                    await sess.provider.close()
                except Exception:
                    pass

        try:
            fut = asyncio.run_coroutine_threadsafe(_close_all(), self.loop)
            try:
                fut.result(timeout=join_timeout)
            except Exception:
                pass
        except Exception:
            pass

        try:
            self.loop.call_soon_threadsafe(self.loop.stop)
        except Exception:
            pass
        try:
            if self.thread.is_alive():
                self.thread.join(timeout=join_timeout)
        except Exception:
            pass

_MANAGER: Optional[StreamManager] = None

def get_manager() -> StreamManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = StreamManager()
    return _MANAGER

# coroutine used by ASGI app shutdown hook
async def shutdown_manager():
    mgr = _MANAGER
    if mgr is not None:
        mgr.shutdown()
