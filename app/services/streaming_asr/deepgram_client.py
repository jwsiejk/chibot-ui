"""Minimal Deepgram streaming client with provider keepalive support."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from typing import Any, Callable

_logger = logging.getLogger(__name__)


class DeepgramClient:
    """Client wrapper responsible for maintaining the Deepgram websocket."""

    def __init__(self) -> None:
        self._ws: Any | None = None
        self._send_lock = asyncio.Lock()
        self._keepalive_interval = max(
            0.0, float(os.getenv("DG_KEEPALIVE_INTERVAL_S", "5.0"))
        )
        self._keepalive_task: asyncio.Task[None] | None = None

    async def connect(self, websocket: Any) -> None:
        """Attach to an already negotiated websocket transport."""

        if websocket is None:
            raise ValueError("websocket must not be None")

        await self._stop_keepalive()
        self._ws = websocket

        if self._keepalive_interval > 0:
            self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def close(self) -> None:
        """Terminate the websocket connection and stop background tasks."""

        await self._stop_keepalive()
        ws = self._ws
        self._ws = None
        if ws is None:
            return
        close: Callable[[], Any] | None = getattr(ws, "close", None)
        if close is None:
            return
        result = close()
        if asyncio.iscoroutine(result) or asyncio.isfuture(result):
            await result

    async def send_json(self, payload: dict[str, Any]) -> None:
        """Send a JSON payload to the provider using the serialized transport."""

        if not isinstance(payload, dict):
            raise TypeError("payload must be a dict")
        message = json.dumps(payload, separators=(",", ":"))
        await self._send_text(message)

    async def _send_text(self, message: str) -> None:
        ws = self._ws
        if ws is None:
            raise RuntimeError("Deepgram websocket not connected")

        sender = getattr(ws, "send", None)
        if sender is None:
            sender = getattr(ws, "send_str", None)
        if sender is None or not callable(sender):
            raise AttributeError("websocket transport missing send method")

        async with self._send_lock:
            result = sender(message)
            if asyncio.iscoroutine(result) or asyncio.isfuture(result):
                await result

    async def _keepalive_loop(self) -> None:
        """Send periodic provider keepalive frames until cancelled."""

        try:
            if not self._socket_is_open():
                return
            await self._send_keepalive()
            while True:
                await asyncio.sleep(self._keepalive_interval)
                if not self._socket_is_open():
                    break
                await self._send_keepalive()
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive
            _logger.exception("Deepgram keepalive loop failed")
        finally:
            self._keepalive_task = None

    async def _send_keepalive(self) -> None:
        await self._send_text(json.dumps({"type": "KeepAlive"}, separators=(",", ":")))

    def _socket_is_open(self) -> bool:
        ws = self._ws
        if ws is None:
            return False
        if getattr(ws, "closed", False):
            return False
        close_code = getattr(ws, "close_code", None)
        if close_code not in (None, 0):
            return False
        return True

    async def _stop_keepalive(self) -> None:
        task = self._keepalive_task
        self._keepalive_task = None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


__all__ = ["DeepgramClient"]
