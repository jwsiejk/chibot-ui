"""Deepgram realtime streaming client."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict

import websockets
from websockets.legacy.client import WebSocketClientProtocol

_logger = logging.getLogger(__name__)

_DEFAULT_LISTEN_URL = "wss://api.deepgram.com/v1/listen"
_DEFAULT_BUFFER_BYTES = 4 * 1024 * 1024


@dataclass
class _StreamState:
    sid: str
    stream_id: str
    websocket: WebSocketClientProtocol
    on_partial: Callable[[str], None]
    on_final: Callable[[str], None]
    on_error: Callable[[str], None]
    on_close: Callable[[int | None, str | None], None] | None
    loop: asyncio.AbstractEventLoop
    chunks: Deque[bytes] = field(default_factory=deque)
    buffered_bytes: int = 0
    drop_logged: bool = False
    closing: bool = False
    sender_task: asyncio.Task[None] | None = None
    receiver_task: asyncio.Task[None] | None = None
    data_event: asyncio.Event = field(default_factory=asyncio.Event)
    finalized: bool = False


class DeepgramClient:
    """Client wrapper responsible for maintaining Deepgram realtime streams."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        url: str | None = None,
        max_buffer_bytes: int | None = None,
    ) -> None:
        self._api_key = (api_key or os.getenv("DEEPGRAM_API_KEY")) or ""
        self._url = url or os.getenv("DEEPGRAM_LISTEN_URL", _DEFAULT_LISTEN_URL)
        self._max_buffer_bytes = max_buffer_bytes or _DEFAULT_BUFFER_BYTES
        if self._max_buffer_bytes <= 0:
            self._max_buffer_bytes = _DEFAULT_BUFFER_BYTES
        self._streams: Dict[str, _StreamState] = {}
        self.idle_close_ms = int(os.getenv("ASR_IDLE_CLOSE_MS", "4000"))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def open_stream(
        self,
        sid: str,
        content_type: str,
        on_partial: Callable[[str], None],
        on_final: Callable[[str], None],
        on_error: Callable[[str], None],
        *,
        stream_id: str,
        on_close: Callable[[int | None, str | None], None] | None = None,
    ) -> None:
        if not isinstance(sid, str) or not sid:
            raise ValueError("sid must be a non-empty string")
        if sid in self._streams:
            return
        if not isinstance(stream_id, str) or not stream_id:
            raise ValueError("stream_id must be a non-empty string")
        loop = asyncio.get_running_loop()
        headers = {"Authorization": f"Token {self._api_key}"}
        try:
            websocket = await websockets.connect(
                self._url,
                extra_headers=headers,
                max_size=None,
                ping_interval=None,
            )
        except Exception as exc:
            _logger.exception("evt=deepgram_connect_failed sid=%s err=%s", sid, exc)
            raise

        start_payload = {
            "type": "StartRequest",
            "metadata": {"content_type": content_type},
        }
        await websocket.send(json.dumps(start_payload, separators=(",", ":")))

        state = _StreamState(
            sid=sid,
            websocket=websocket,
            on_partial=on_partial,
            on_final=on_final,
            on_error=on_error,
            on_close=on_close,
            loop=loop,
            stream_id=stream_id,
        )
        state.sender_task = loop.create_task(self._sender_loop(state), name=f"dg-send-{sid}")
        state.receiver_task = loop.create_task(
            self._receiver_loop(state), name=f"dg-recv-{sid}"
        )
        self._streams[sid] = state
        loop.call_soon(
            _logger.info,
            "evt=dg_stream_open sid=%s stream_id=%s url=%s",
            sid,
            stream_id,
            self._url,
        )

    def send_audio(self, sid: str, chunk: bytes) -> None:
        state = self._streams.get(sid)
        if state is None or state.closing:
            return
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise TypeError("chunk must be bytes-like")
        data = bytes(chunk)
        if not data:
            return

        state.chunks.append(data)
        state.buffered_bytes += len(data)
        if state.buffered_bytes > self._max_buffer_bytes:
            dropped = 0
            while state.chunks and state.buffered_bytes > self._max_buffer_bytes:
                removed = state.chunks.popleft()
                dropped += len(removed)
                state.buffered_bytes -= len(removed)
            if dropped and not state.drop_logged:
                state.drop_logged = True
                _logger.warning(
                    "evt=deepgram_backpressure sid=%s dropped_bytes=%d", sid, dropped
                )
        state.data_event.set()

    def close_stream(self, sid: str) -> None:
        state = self._streams.pop(sid, None)
        if state is None:
            return
        state.closing = True
        state.data_event.set()
        if state.receiver_task is not None:
            state.receiver_task.cancel()
        if state.sender_task is not None:
            state.sender_task.cancel()
        state.loop.create_task(self._shutdown(state))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    async def _sender_loop(self, state: _StreamState) -> None:
        try:
            while True:
                await state.data_event.wait()
                state.data_event.clear()
                while state.chunks:
                    chunk = state.chunks.popleft()
                    state.buffered_bytes -= len(chunk)
                    await state.websocket.send(chunk)
                if state.closing:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            _logger.exception("evt=deepgram_send_failed sid=%s err=%s", state.sid, exc)
            state.on_error(state.sid, str(exc))

    async def _receiver_loop(self, state: _StreamState) -> None:
        websocket = state.websocket
        try:
            async for message in websocket:
                if isinstance(message, bytes):
                    continue
                try:
                    payload = json.loads(message)
                except json.JSONDecodeError:
                    continue
                self._handle_message(state, payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            _logger.exception("evt=deepgram_recv_failed sid=%s err=%s", state.sid, exc)
            state.on_error(state.sid, str(exc))
        finally:
            await self._finalize_stream(state)

    async def _shutdown(self, state: _StreamState) -> None:
        try:
            await state.websocket.close()
        except Exception:  # pragma: no cover - defensive
            _logger.debug("evt=deepgram_ws_close_error sid=%s", state.sid, exc_info=True)
        await self._finalize_stream(state)

    async def _finalize_stream(self, state: _StreamState) -> None:
        if state.finalized:
            return
        state.finalized = True
        try:
            await state.websocket.wait_closed()
        except Exception:  # pragma: no cover - defensive
            pass
        code = state.websocket.close_code
        reason = (state.websocket.close_reason or "").replace('"', '\\"')
        _logger.info(
            'evt=dg_stream_closed sid=%s stream_id=%s code=%s reason="%s"',
            state.sid,
            state.stream_id,
            code if code is not None else 0,
            reason,
        )
        callback = state.on_close
        if callback is not None:
            try:
                callback(code, state.websocket.close_reason)
            except Exception:  # pragma: no cover - defensive
                _logger.exception("evt=deepgram_close_callback_failed sid=%s", state.sid)
        state.on_close = None
        self._streams.pop(state.sid, None)

    def _handle_message(self, state: _StreamState, payload: dict) -> None:
        message_type = payload.get("type")
        if message_type == "Results":
            channel = payload.get("channel") or {}
            alternatives = channel.get("alternatives") or []
            if not alternatives:
                return
            transcript = alternatives[0].get("transcript", "").strip()
            if not transcript:
                return
            is_final = bool(channel.get("is_final") or payload.get("speech_final"))
            if is_final:
                state.on_final(state.sid, transcript)
            else:
                state.on_partial(state.sid, transcript)
            return
        if message_type == "error" or message_type == "Error":
            error = payload.get("error") or payload.get("message") or str(payload)
            state.on_error(state.sid, error)
            return
        if "error" in payload:
            state.on_error(state.sid, str(payload.get("error")))


__all__ = ["DeepgramClient"]
