# app/services/streaming_asr/stream_manager.py
from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from typing import Deque, Dict, Optional, Any

from app.ws.bus import bus
from app.config_store import get_config
from .deepgram_client import DeepgramClient

# In-proc metrics & breaker
_METRICS: Dict[str, int] = {
    "partials": 0,
    "finals": 0,
    "provider_errors": 0,
    "sessions": 0,
}
_CB_OPEN_UNTIL_MS: int = 0
_CB_BACKOFF_MS: int = 60_000  # 60s backoff on provider errors


def _now_ms() -> int:
    return int(time.time() * 1000)


def _cb_opened() -> bool:
    return _now_ms() < _CB_OPEN_UNTIL_MS


def _cb_trip() -> None:
    global _CB_OPEN_UNTIL_MS
    _CB_OPEN_UNTIL_MS = _now_ms() + _CB_BACKOFF_MS


class StreamingASRManager:
    """
    Orchestrates per-session live ASR with Deepgram.
    - Opens provider session on first enqueue.
    - Flushes any queued slices immediately after connect.
    - Emits user_partial/user_final over the WS bus.
    """

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thr = threading.Thread(target=self._loop.run_forever, name="asr-loop", daemon=True)
        self._thr.start()

        self._queues: Dict[str, Deque[bytes]] = {}
        self._tasks: Dict[str, asyncio.Future] = {}
        self._turn_id: Dict[str, str] = {}  # last user_msg_id per session (optional)

    # ---- public API ---------------------------------------------------------

    def enqueue(self, sid: str, item: Any) -> None:
        """
        Accepts either raw bytes or a dict {"data": bytes, "user_msg_id": str, "chunk_seq": int}
        """
        if isinstance(item, dict):
            data = item.get("data") or b""
            user_msg_id = str(item.get("user_msg_id") or "")
            if user_msg_id:
                self._turn_id[sid] = user_msg_id
        else:
            data = item

        if not data:
            return

        q = self._queues.setdefault(sid, deque())
        q.append(data)
        # ensure a running task for this session
        if sid not in self._tasks or self._tasks[sid].done():
            fut = asyncio.run_coroutine_threadsafe(self._run_session(sid), self._loop)
            self._tasks[sid] = fut

    def shutdown(self) -> None:
        try:
            for f in list(self._tasks.values()):
                try:
                    f.cancel()
                except Exception:
                    pass
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass

    # ---- internal -----------------------------------------------------------

    async def _run_session(self, sid: str) -> None:
        global _METRICS
        if _cb_opened():
            # surface breaker to the UI via bus (optional)
            bus.broadcast(sid, {"type": "asr_error", "error": "provider_backoff"})
            return

        client: Optional[DeepgramClient] = None
        try:
            cfg = get_config() or {}
        except Exception:
            cfg = {}

        try:
            client = DeepgramClient(cfg)
            await client.connect()
            _METRICS["sessions"] += 1
        except Exception:
            _METRICS["provider_errors"] += 1
            _cb_trip()
            bus.broadcast(sid, {"type": "asr_error", "error": "provider_connect"})
            return

        # receiver task: forward partials/finals to bus
        async def _rx():
            try:
                async for ev in client.events():
                    t = ev.get("type")
                    text = ev.get("text") or ""
                    if not text:
                        continue
                    if t == "user_partial":
                        _METRICS["partials"] += 1
                    elif t == "user_final":
                        _METRICS["finals"] += 1
                    bus.broadcast(sid, {"type": t, "text": text})
            except Exception:
                # swallow; sender side will close
                pass

        rx_task = asyncio.create_task(_rx())

        # send loop: flush backlog then idle-wait for a short window
        try:
            idle_ms = 0
            last_send = _now_ms()

            while True:
                # flush any queued chunks
                q = self._queues.get(sid)
                while q and q:
                    data = q.popleft()
                    await client.send_bytes(data)
                    last_send = _now_ms()

                # simple idle termination (no chunks for ~2s)
                if _now_ms() - last_send > 2000:
                    break

                await asyncio.sleep(0.02)

        except Exception:
            pass
        finally:
            try:
                rx_task.cancel()
            except Exception:
                pass
            try:
                await client.close()
            except Exception:
                pass

    # ------------------------------------------------------------------------


_MANAGER: Optional[StreamingASRManager] = None


def get_manager() -> StreamingASRManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = StreamingASRManager()
    return _MANAGER


async def shutdown_manager():
    mgr = _MANAGER
    if mgr is not None:
        mgr.shutdown()


def get_streaming_status() -> Dict[str, object]:
    return {
        "breaker_open": _cb_opened(),
        "provider_errors": _METRICS["provider_errors"],
        "partials": _METRICS["partials"],
        "finals": _METRICS["finals"],
        "sessions": _METRICS["sessions"],
    }
