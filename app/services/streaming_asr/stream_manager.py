from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, Optional

from app.services.streaming_asr.deepgram_client import DeepgramClient

# Your bus must exist; Diagnostics listens for these events.
from app.ws.bus import bus
from app.api_v1.admin import _emit  # broadcasting: bus.broadcast(session_id, payload)

# simple circuit-breaker + counters (observable via diagnostics endpoint)
_METRICS: Dict[str, int] = {
    "provider_errors": 0,
    "partials": 0,
    "finals": 0,
    "sessions": 0,
}
_CB_BACKOFF_MS = 4000
_CB_OPEN_UNTIL = 0


def _now_ms() -> int:
    return int(time.time() * 1000)


def _cb_open() -> bool:
    return _now_ms() < _CB_OPEN_UNTIL


def _cb_trip() -> None:
    global _CB_OPEN_UNTIL
    _CB_OPEN_UNTIL = _now_ms() + _CB_BACKOFF_MS


class StreamingASRManager:
    """
    Manages a provider session per Ask Chip session id.
    Accepts mic slices via enqueue(); opens Deepgram; relays partial/final text over the WS bus.
    """

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thr = threading.Thread(target=self._loop.run_forever, name="asr-loop", daemon=True)
        self._thr.start()
        self._queues: Dict[str, Deque[bytes]] = {}
        self._tasks: Dict[str, asyncio.Future] = {}
        self._user_msg_id: Dict[str, str] = {}

    def enqueue(self, sid: str, item: Any) -> None:
        """
        item may be:
          - raw bytes
          - dict: {"data": bytes, "user_msg_id": str, "chunk_seq": int}
        """
        if isinstance(item, dict):
            data = item.get("data") or b""
            umid = str(item.get("user_msg_id") or "")
            if umid:
                self._user_msg_id[sid] = umid
        else:
            data = bytes(item or b"")
        if not data:
            return

        q = self._queues.setdefault(sid, deque())
        q.append(data)

        task = self._tasks.get(sid)
        if task is None or task.done():
            fut = asyncio.run_coroutine_threadsafe(self._run_session(sid), self._loop)
            self._tasks[sid] = fut

    def shutdown(self) -> None:
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass

    # -------------------- internals --------------------

    async def _run_session(self, sid: str) -> None:
        if _cb_open():
            bus.broadcast(sid, {"type": "asr_error", "error": "breaker_open"})
            return

        _METRICS["sessions"] += 1
        client = DeepgramClient()

        # connect
        try:
            await client.connect()
            bus.broadcast(sid, {"type": "asr_open"});
            try: _emit('asr', label='asr_open', session_id=sid)
            except Exception: pass
        except Exception as e:
            _METRICS["provider_errors"] += 1
            _cb_trip()
            bus.broadcast(sid, {"type": "asr_error", "error": f"provider_connect:{e.__class__.__name__}:{str(e)}"});
            try: _emit('asr', label='asr_error', session_id=sid, error=f'provider_connect:{e.__class__.__name__}')
            except Exception: pass
            return

        async def _rx():
            try:
                async for ev in client.events():
                    t = ev.get("type")
                    if t == "user_partial":
                        _METRICS["partials"] += 1
                    elif t == "user_final":
                        _METRICS["finals"] += 1
                    bus.broadcast(
                        sid,
                        {
                            "type": t,
                            "text": ev.get("text", ""),
                            "user_msg_id": self._user_msg_id.get(sid),
                        },
                    )
            except Exception as e:
                try: _emit('asr', label='asr_error', session_id=sid, error=f'rx:{e.__class__.__name__}:{str(e)}')
                except Exception: pass
                pass  # sender handles close

        rx_task = asyncio.create_task(_rx())

        try:
            q = self._queues.get(sid, deque())
            while q:
                chunk = q.popleft()
                try:
                    await client.send(chunk)
                except Exception as e:
                    _METRICS["provider_errors"] += 1
                    _cb_trip()
                    bus.broadcast(sid, {"type": "asr_error", "error": f"send:{e.__class__.__name__}:{str(e)}"});
                    try: _emit('asr', label='asr_error', session_id=sid, error=f'send:{e.__class__.__name__}')
                    except Exception: pass
                    break

            # give the provider a short drain window to emit a final
            await asyncio.sleep(0.6)
        finally:
            try:
                await client.close()
            except Exception:
                pass
            try:
                rx_task.cancel()
            except Exception:
                pass


# ------------- module-level helpers used by diagnostics ----------------------

_MANAGER: Optional[StreamingASRManager] = None


def get_manager() -> StreamingASRManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = StreamingASRManager()
    return _MANAGER


def shutdown_manager() -> None:
    m = _MANAGER
    if m:
        m.shutdown()


def get_streaming_status() -> Dict[str, object]:
    return {
        "breaker_open": _cb_open(),
        "provider_errors": _METRICS["provider_errors"],
        "partials": _METRICS["partials"],
        "finals": _METRICS["finals"],
        "sessions": _METRICS["sessions"],
    }
