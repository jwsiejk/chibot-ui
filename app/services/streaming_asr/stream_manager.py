# app/services/streaming_asr/stream_manager.py
from __future__ import annotations

import asyncio
import time
import threading
from collections import deque
from typing import Deque, Dict, Optional

from app.ws.bus import bus                      # existing bus instance
from .deepgram_client import FakeDeepgramClient # swap to real provider later

# Lightweight counters (in-proc). If you already have a metrics sink, wire it there.
_METRICS = {
    "partials": 0,
    "finals": 0,
    "queue_drops": 0,
    "provider_errors": 0,
    "sessions": 0,
}

# Simple circuit breaker: open after N provider errors, stay open for 'cooldown' seconds.
_CB = {
    "open_until": 0.0,   # epoch seconds
    "error_count": 0,
    "trip_threshold": 8,  # consecutive errors before opening
    "cooldown": 60.0,     # seconds to keep open
}

def _cb_opened() -> bool:
    return time.time() < _CB["open_until"]

def _cb_trip():
    _CB["open_until"] = time.time() + _CB["cooldown"]
    _CB["error_count"] = 0

def _cb_note_error():
    _CB["error_count"] += 1
    if _CB["error_count"] >= _CB["trip_threshold"]:
        _cb_trip()

class StreamSession:
    def __init__(self, session_id: str, provider):
        self.session_id = session_id
        self.provider = provider
        self.queue: Deque[bytes] = deque(maxlen=32)  # backpressure window
        self.last_enqueue = time.time()
        self.closed = False

    def put(self, data: bytes) -> None:
        if len(self.queue) == self.queue.maxlen:
            _METRICS["queue_drops"] += 1
        else:
            self.queue.append(data)
            self.last_enqueue = time.time()

class StreamManager:
    """
    Holds one streaming ASR client per user session.
    Enqueues Opus/PCM timeslices (from POST /api/v1/voice/stt/stream),
    feeds provider, and emits user_partial / user_final to the WS bus.
    """
    def __init__(self) -> None:
        self.sessions: Dict[str, StreamSession] = {}
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def _get_provider(self):
        # Deterministic fake for now; replace with the real Deepgram client later.
        return FakeDeepgramClient({})

    def enqueue(self, session_id: str, data: bytes) -> None:
        if _cb_opened():
            # Drop quietly; route should return a 503 so client can back off
            return
        sess = self.sessions.get(session_id)
        if not sess:
            provider = self._get_provider()
            sess = self.sessions[session_id] = StreamSession(session_id, provider)
            _METRICS["sessions"] += 1
            asyncio.run_coroutine_threadsafe(self._run_session(sess), self.loop)
        sess.put(data)

    async def _run_session(self, sess: StreamSession) -> None:
        try:
            await sess.provider.connect()
        except Exception:
            _METRICS["provider_errors"] += 1
            _cb_note_error()
            return

        IDLE_TIMEOUT = 9.0
        try:
            while not sess.closed:
                # idle close
                if (time.time() - sess.last_enqueue) > IDLE_TIMEOUT and not sess.queue:
                    try:
                        await sess.provider.close()
                    finally:
                        sess.closed = True
                        break

                # feed provider
                if sess.queue:
                    data = sess.queue.popleft()
                    try:
                        await sess.provider.send(data)
                    except Exception:
                        _METRICS["provider_errors"] += 1
                        _cb_note_error()
                        # emit a one-time notice to the client/admin log (non-blocking)
                        bus.broadcast(sess.session_id, {
                            "type": "system_notice",
                            "level": "warn",
                            "text": "Streaming ASR provider error; will retry.",
                        })
                        await asyncio.sleep(0.05)
                        continue

                    # Fake provider behavior: partials on counts 2/4, final on 6.
                    count = getattr(sess.provider, "_count", 0)
                    if count in (2, 4):
                        _METRICS["partials"] += 1
                        bus.broadcast(sess.session_id, {
                            "type": "user_partial",
                            "text": f"hello {count}"
                        })
                    if count == 6:
                        _METRICS["finals"] += 1
                        bus.broadcast(sess.session_id, {
                            "type": "user_final",
                            "text": "final hello"
                        })

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
