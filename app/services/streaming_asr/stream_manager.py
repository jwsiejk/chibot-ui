from __future__ import annotations

import asyncio
import time
import threading
from collections import deque
from typing import Deque, Dict, Optional

from app.ws.bus import bus
from .deepgram_client import FakeDeepgramClient

_METRICS = {
    "partials": 0,
    "finals": 0,
    "queue_drops": 0,
    "provider_errors": 0,
    "sessions": 0,
}

_CB = {
    "open_until": 0.0,
    "error_count": 0,
    "trip_threshold": 8,
    "cooldown": 60.0,
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
        self.queue: Deque[bytes] = deque(maxlen=32)
        self.last_enqueue = time.time()
        self.closed = False

    def put(self, data: bytes) -> None:
        if len(self.queue) == self.queue.maxlen:
            _METRICS["queue_drops"] += 1
        else:
            self.queue.append(data)
            self.last_enqueue = time.time()

class StreamManager:
    def __init__(self) -> None:
        self.sessions: Dict[str, StreamSession] = {}
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def _get_provider(self):
        return FakeDeepgramClient({})

    def enqueue(self, session_id: str, data: bytes) -> None:
        if _cb_opened():
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
                if (time.time() - sess.last_enqueue) > IDLE_TIMEOUT and not sess.queue:
                    try: await sess.provider.close()
                    finally:
                        sess.closed = True
                        break

                if sess.queue:
                    data = sess.queue.popleft()
                    try:
                        await sess.provider.send(data)
                    except Exception:
                        _METRICS["provider_errors"] += 1
                        _cb_note_error()
                        bus.broadcast(sess.session_id, {
                            "type": "system_notice",
                            "level": "warn",
                            "text": "Streaming ASR provider error; will retry.",
                        })
                        await asyncio.sleep(0.05)
                        continue

                    count = getattr(sess.provider, "_count", 0)
                    if count in (2, 4):
                        _METRICS["partials"] += 1
                        bus.broadcast(sess.session_id, {"type":"user_partial","text":f"hello {count}"})
                    if count == 6:
                        _METRICS["finals"] += 1
                        bus.broadcast(sess.session_id, {"type":"user_final","text":"final hello"})

                await asyncio.sleep(0.01)
        finally:
            try: await sess.provider.close()
            except Exception: pass

    def shutdown(self, join_timeout: float = 2.0) -> None:
        if not self.thread or not self.loop:
            return

        async def _close_all():
            for sess in list(self.sessions.values()):
                try: await sess.provider.close()
                except Exception: pass

        try:
            fut = asyncio.run_coroutine_threadsafe(_close_all(), self.loop)
            try: fut.result(timeout=join_timeout)
            except Exception: pass
        except Exception:
            pass

        try: self.loop.call_soon_threadsafe(self.loop.stop)
        except Exception: pass
        try:
            if self.thread.is_alive(): self.thread.join(timeout=join_timeout)
        except Exception: pass

_MANAGER: Optional[StreamManager] = None

def get_manager() -> StreamManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = StreamManager()
    return _MANAGER

async def shutdown_manager():
    mgr = _MANAGER
    if mgr is not None:
        mgr.shutdown()

# ---- status for diagnostics ----
def get_streaming_status() -> Dict[str, object]:
    return {
        "breaker_open": _cb_opened(),
        "provider_errors": _METRICS["provider_errors"],
        "partials": _METRICS["partials"],
        "finals": _METRICS["finals"],
        "sessions": _METRICS["sessions"],
    }
