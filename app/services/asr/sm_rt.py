"""Speechmatics realtime websocket client (PCM-only)."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Literal

import websockets
from websockets.client import WebSocketClientProtocol
from websockets.exceptions import ConnectionClosed, ConnectionClosedError

from app.telemetry import bus as telemetry_bus
from app.telemetry.events import (
    ASR_KEEPALIVE_PING,
    ASR_VENDOR_CLOSE_ACK,
    ASR_VENDOR_CONNECT_INTENT,
    SM_FINAL,
    SM_NOTICE,
    SM_PARTIAL,
)
from app.voice_v2 import (
    EVT_ASR_CLOSED,
    EVT_ASR_FINAL,
    EVT_ASR_OPEN,
    EVT_ASR_PARTIAL,
    EVT_ASR_READY,
    EVT_CLIENT_LOG,
)

__all__ = [
    "ASR_KEEPALIVE_PING",
    "ASR_VENDOR_CLOSE_ACK",
    "ASR_VENDOR_CONNECT_INTENT",
    "SM_FINAL",
    "SM_NOTICE",
    "SM_PARTIAL",
    "SMRealtimeClient",
]


_log = logging.getLogger(__name__)

class SMRealtimeClient:
    """Minimal Speechmatics realtime client with telemetry hooks."""

    VENDOR = "speechmatics"
    SOURCE = "speechmatics_client"

    _READY_MESSAGES = {"RecognitionStarted", "recognition_started"}
    _PARTIAL_MESSAGES = {
        "AddPartialTranscript",
        "PartialTranscript",
        "AddPartial",
        "add_partial_transcript",
    }
    _FINAL_MESSAGES = {
        "AddTranscript",
        "Transcript",
        "FinalTranscript",
        "AddFinalTranscript",
    }
    _CLOSE_ACK_MESSAGES = {
        "EndOfTranscript",
        "RecognitionEnded",
        "EndOfStreamAcknowledged",
        "EndOfStream",
        "SessionEnded",
    }

    PCM_QUEUE_MAX = 25
    READY_TIMEOUT_S = 5.0
    CLOSE_ACK_TIMEOUT_S = 5.0
    KEEPALIVE_INTERVAL_S = 30.0

    def __init__(
        self,
        sid: str,
        *,
        telemetry_bus=telemetry_bus,
        on_ready: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_partial: Optional[Callable[[str, int], None]] = None,
        on_final: Optional[Callable[[str, int], None]] = None,
        on_notice: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_closed: Optional[Callable[[str], None]] = None,
        ready_timeout_s: Optional[float] = None,
        close_ack_timeout_s: Optional[float] = None,
        keepalive_interval_s: Optional[float] = None,
    ) -> None:
        if not sid:
            raise ValueError("sid must be provided")
        self._sid = sid
        self.session_id = sid
        self.turn_id: Optional[str] = None
        self._bus = telemetry_bus
        self._state = "idle"
        self._state_lock = asyncio.Lock()
        self._ws: WebSocketClientProtocol | None = None
        self._endpoint_url: str | None = None
        self._url: str | None = None
        self._connected_at = 0.0
        self._last_activity = time.monotonic()
        self._partial_seq = 0
        self._ready_event = asyncio.Event()
        self._close_ack_event = asyncio.Event()
        self._stop_event = asyncio.Event()
        self._pcm_queue: asyncio.Queue[bytes | object] = asyncio.Queue(maxsize=self.PCM_QUEUE_MAX)
        self._pcm_sentinel: object = object()
        self._receiver_task: asyncio.Task[None] | None = None
        self._sender_task: asyncio.Task[None] | None = None
        self._ws_ping_task: asyncio.Task[None] | None = None
        self._closing = False
        self._closed_event = asyncio.Event()
        self._close_reason: str | None = None
        self._sent_end_of_stream = False
        self._seq_counter = 0
        self._last_seq_no: Optional[int] = None
        self._first_pcm_sent_at: float | None = None
        self._ready_timeout_s = max(0.1, ready_timeout_s or self.READY_TIMEOUT_S)
        self._close_ack_timeout_s = max(0.1, close_ack_timeout_s or self.CLOSE_ACK_TIMEOUT_S)
        self._keepalive_interval_s = max(1.0, keepalive_interval_s or self.KEEPALIVE_INTERVAL_S)

        self._on_ready = on_ready or (lambda info: None)
        self._on_partial = on_partial or (lambda text, latency: None)
        self._on_final = on_final or (lambda text, latency: None)
        self._on_notice = on_notice or (lambda meta: None)
        self._on_closed = on_closed or (lambda reason: None)

        self._input_start_ms: Optional[int] = None
        self._first_partial_logged = False
        self._first_final_logged = False
        self._first_pcm_logged = False
        self._eos_logged = False
        self._eos_suppress_logged = False

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------
    @property
    def state(self) -> str:
        """Return the current connection state."""

        return self._state

    # ------------------------------------------------------------------
    # Lifecycle management
    # ------------------------------------------------------------------
    async def open(
        self, *, endpoint_url: str, jwt_token: str | None, params: Dict[str, Any]
    ) -> None:
        """Open the Speechmatics websocket and wait for RecognitionStarted."""

        if not isinstance(params, dict):
            raise TypeError("params must be a dict")
        if not endpoint_url:
            raise ValueError("endpoint_url must be provided")

        async with self._state_lock:
            if self._state not in {"idle", "closed"}:
                raise RuntimeError(f"client state {self._state} cannot connect")
            self._state = "opening"

        self._stop_event = asyncio.Event()
        self._ready_event = asyncio.Event()
        self._close_ack_event = asyncio.Event()
        self._closed_event = asyncio.Event()
        self._close_reason = None
        self._sent_end_of_stream = False
        self._pcm_queue = asyncio.Queue(maxsize=self.PCM_QUEUE_MAX)
        self._seq_counter = 0
        self._last_seq_no = None
        self._first_pcm_sent_at = None
        self._first_pcm_logged = False
        self._eos_logged = False
        self._eos_suppress_logged = False

        url = endpoint_url.strip()
        if not url:
            raise ValueError("endpoint_url must be provided")
        if not url.startswith("wss://"):
            raise ValueError(f"Invalid Speechmatics endpoint (must be wss://): {url!r}")
        self._endpoint_url = url
        self._publish_event(
            ASR_VENDOR_CONNECT_INTENT,
            {
                "vendor": self.VENDOR,
                "meta": {"reason": "connect", "endpoint": url},
            },
        )

        headers: dict[str, str] = {}
        if jwt_token:
            ws_url = f"{url}?jwt={jwt_token}"
            auth_mode = "jwt"
        else:
            ws_url = url
            from app.config import SPEECHMATICS_API_KEY

            if not SPEECHMATICS_API_KEY:
                raise RuntimeError("Speechmatics API key missing for header auth")
            headers["Authorization"] = f"Bearer {SPEECHMATICS_API_KEY}"
            auth_mode = "api_key_header"

        _log.info("evt=sm_ws_connect url=%s auth=%s", ws_url, auth_mode)
        self._url = ws_url

        async def _connect(url: str, hdrs: dict[str, str] | None):
            # Compatibility across websockets versions (arg name differences)
            try:
                return await websockets.connect(
                    url,
                    extra_headers=hdrs or None,
                    max_size=None,
                    ping_interval=20,
                    ping_timeout=20,
                )
            except TypeError:
                try:
                    return await websockets.connect(
                        url,
                        additional_headers=hdrs or None,
                        max_size=None,
                        ping_interval=20,
                        ping_timeout=20,
                    )
                except TypeError:
                    return await websockets.connect(
                        url,
                        headers=hdrs or None,
                        max_size=None,
                        ping_interval=20,
                        ping_timeout=20,
                    )

        try:
            ws = await _connect(ws_url, headers)
        except Exception:
            async with self._state_lock:
                self._state = "closed"
            raise

        self._ws = ws
        self._connected_at = time.monotonic()
        self._last_activity = self._connected_at

        try:
            _log.info("evt=sm_startrecognition payload=%s", params)
            await self._send_json(params)
        except ConnectionClosedError as exc:
            if getattr(exc, "code", None) == 4001:
                _log.error(
                    "evt=sm_auth_failed detail=not_authorised hint='Check region entitlement or header auth key'"
                )
            raise

        self._publish_event(
            EVT_ASR_OPEN,
            {
                "vendor": self.VENDOR,
                "meta": {"url": ws_url},
            },
        )

        extra_messages: list[dict] = []
        try:
            extra_messages = await self._await_ready(ws)
        except Exception:
            with contextlib.suppress(Exception):
                await ws.close()
            await self._finalize_close(reason="ready_failed", publish_close=True)
            raise

        self._ready_event.set()

        self._receiver_task = asyncio.create_task(self._receive_loop())
        self._sender_task = asyncio.create_task(self._pcm_sender_loop())
        self._ws_ping_task = asyncio.create_task(self._ws_ping_loop())

        for message in extra_messages:
            self._process_message(message)

    async def send_pcm(self, chunk: bytes) -> None:
        """Queue PCM16 audio bytes for transmission."""

        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise TypeError("chunk must be bytes-like")
        data = bytes(chunk)
        if not data:
            return
        if len(data) % 2 != 0:
            raise ValueError("pcm chunk must contain complete 16-bit samples")

        if self._state != "open":
            self._emit_notice({"kind": "pcm_ignored_state", "state": self._state})
            return

        queue = self._pcm_queue
        if queue.full():
            try:
                dropped = queue.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover - defensive
                dropped = None
            else:
                if dropped is not None and dropped is not self._pcm_sentinel:
                    self._emit_notice(
                        {"kind": "backpressure_drop", "bytes": len(dropped)}
                    )
                queue.task_done()
        await queue.put(data)
        if not self._first_pcm_logged:
            try:
                _log.info("evt=sm_pcm_enqueue_first bytes=%d", len(data))
            except Exception:  # pragma: no cover - defensive
                pass
            else:
                self._first_pcm_logged = True
        if self._first_pcm_sent_at is None:
            try:
                self._first_pcm_sent_at = time.monotonic()
            except Exception:  # pragma: no cover - defensive
                self._first_pcm_sent_at = 0.0

    async def send_end_of_stream(self) -> None:
        """Send the vendor end-of-stream marker and await acknowledgement."""

        if self._state != "open":
            return
        if self._sent_end_of_stream:
            await self._wait_for_close_ack()
            return

        if self._last_seq_no is None:
            detail = {
                "session_id": self.session_id,
                "turn_id": self.turn_id,
                "reason": "no_audio",
            }
            self._emit_hub_log("asr.eos.skipped", detail)
            self._emit_notice({"kind": "eos_skipped_no_audio"})
            self._sent_end_of_stream = True
            self._close_ack_event.set()
            return

        self._sent_end_of_stream = True
        detail = {
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "last_seq_no": self._last_seq_no,
        }
        self._emit_hub_log("asr.eos.sent", detail)
        await self._send_end_of_stream()
        await self._wait_for_close_ack()

    async def close(self) -> None:
        """Gracefully close the websocket connection."""

        if self._state in {"idle", "closed"}:
            return
        if self._closing:
            await self._closed_event.wait()
            return

        self._closing = True
        await self._stop_sender()
        await self._stop_receiver()
        await self._stop_ws_ping()

        ws = self._ws
        if ws is not None:
            try:
                await ws.close(code=1000)
            except Exception:  # pragma: no cover - defensive
                pass

        await self._finalize_close(reason=self._close_reason or "client_close")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    async def _await_ready(self, ws: WebSocketClientProtocol) -> list[dict]:
        """Wait for the RecognitionStarted message."""

        deadline = self._ready_timeout_s
        extra: list[dict] = []
        start = time.monotonic()
        while True:
            remaining = deadline - (time.monotonic() - start)
            if remaining <= 0:
                raise TimeoutError("speechmatics ready timeout")
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            self._last_activity = time.monotonic()
            message = self._decode_message(raw)
            if not isinstance(message, dict):
                continue
            message_type = self._message_type(message)
            if message_type in self._READY_MESSAGES:
                self._handle_ready(message)
                break
            extra.append(message)
        return extra

    async def _receive_loop(self) -> None:
        ws = self._ws
        if ws is None:
            return
        try:
            async for raw in ws:
                self._last_activity = time.monotonic()
                message = self._decode_message(raw)
                self._process_message(message)
        except asyncio.CancelledError:  # pragma: no cover - cooperative cancel
            raise
        except ConnectionClosed as exc:
            self._close_reason = exc.reason or "vendor_closed"
        except Exception:  # pragma: no cover - defensive
            _log.exception("speechmatics receive loop error")
            self._close_reason = "receive_error"
        finally:
            await self._finalize_close(reason=self._close_reason or "vendor_closed")

    async def _pcm_sender_loop(self) -> None:
        await self._ready_event.wait()
        try:
            while not self._stop_event.is_set():
                try:
                    item = await asyncio.wait_for(self._pcm_queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                if item is self._pcm_sentinel:
                    self._pcm_queue.task_done()
                    break
                if not isinstance(item, (bytes, bytearray)):
                    self._pcm_queue.task_done()
                    continue
                data = bytes(item)
                ws = self._ws
                if ws is None:
                    self._pcm_queue.task_done()
                    break
                try:
                    next_seq = self._seq_counter + 1
                    await ws.send(data)
                    self._last_activity = time.monotonic()
                    if self._first_pcm_sent_at is None:
                        self._first_pcm_sent_at = self._last_activity
                    self._seq_counter = next_seq
                    self._last_seq_no = next_seq
                except ConnectionClosed:
                    self._pcm_queue.task_done()
                    break
                except Exception:  # pragma: no cover - defensive
                    _log.exception("speechmatics pcm send failed")
                    self._pcm_queue.task_done()
                    break
                else:
                    self._pcm_queue.task_done()
        except asyncio.CancelledError:  # pragma: no cover - cooperative cancel
            raise

    async def _ws_ping_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(self._keepalive_interval_s)
                if self._stop_event.is_set():
                    break
                if self._state != "open":
                    continue
                now = time.monotonic()
                if now - self._last_activity < self._keepalive_interval_s:
                    continue
                ws = self._ws
                if ws is None:
                    continue
                try:
                    pong_waiter = await ws.ping()
                except ConnectionClosed:
                    break
                except Exception:  # pragma: no cover - defensive
                    _log.warning("speechmatics ws ping send failed", exc_info=True)
                    break
                try:
                    timeout_s = max(1.0, self._keepalive_interval_s / 2)
                    latency_s = await asyncio.wait_for(pong_waiter, timeout=timeout_s)
                except asyncio.TimeoutError:
                    _log.warning(
                        "speechmatics ws ping timeout",
                        extra={"timeout_s": timeout_s},
                    )
                    continue
                except ConnectionClosed:
                    break
                except Exception:  # pragma: no cover - defensive
                    _log.warning("speechmatics ws ping wait failed", exc_info=True)
                    continue
                else:
                    self._last_activity = time.monotonic()
                    self._publish_event(
                        ASR_KEEPALIVE_PING,
                        {
                            "vendor": self.VENDOR,
                            "meta": {
                                "idle_ms": int(self._keepalive_interval_s * 1000),
                                "latency_ms": int(latency_s * 1000),
                            },
                        },
                    )
        except asyncio.CancelledError:  # pragma: no cover
            raise

    def _decode_message(self, raw: Any) -> Any:
        if isinstance(raw, bytes):
            return raw
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except json.JSONDecodeError as exc:
                self._emit_notice(
                    {"kind": "json_decode_error", "error": str(exc), "raw": raw[:80]}
                )
                return {}
        return raw

    def _process_message(self, message: Any) -> None:
        if isinstance(message, dict):
            self._handle_dict_message(message)
        elif message:
            self._emit_notice(
                {
                    "kind": "unexpected_binary",
                    "type": type(message).__name__,
                }
            )

    def _handle_dict_message(self, message: Dict[str, Any]) -> None:
        message_type = self._message_type(message)
        if not message_type:
            self._emit_notice({"kind": "unknown_message", "payload": message})
            return

        if message_type in self._READY_MESSAGES:
            self._handle_ready(message)
            return
        if message_type in self._PARTIAL_MESSAGES:
            self._handle_partial(message)
            return
        if message_type in self._FINAL_MESSAGES:
            self._handle_final(message)
            return
        if message_type in self._CLOSE_ACK_MESSAGES:
            self._handle_close_ack(message, message_type)
            return
        if message_type.lower() in {"warning", "info"}:
            self._emit_notice({"kind": message_type.lower(), "payload": message})
            return
        if message_type.lower() == "error":
            self._emit_notice({"kind": "vendor_error", "payload": message})
            self._close_reason = "vendor_error"
            return
        self._emit_notice({"kind": "unhandled_message", "payload": message})

    def _handle_ready(self, message: Dict[str, Any]) -> None:
        if self._state != "open":
            self._state = "open"
        self._input_start_ms = self._now_ms()
        self._first_partial_logged = False
        self._first_final_logged = False
        meta = self._extract_meta(message)
        self._publish_event(EVT_ASR_READY, {"vendor": self.VENDOR, "meta": meta})
        self._call_callback(self._on_ready, meta)

    def _handle_partial(self, message: Dict[str, Any]) -> None:
        text = self._extract_text(message)
        if not text:
            return
        latency_ms = self._extract_latency_ms(message)
        self._maybe_emit_first_token_latency("partial")
        self._partial_seq += 1
        payload = {
            "text": text,
            "latency_ms": latency_ms,
            "partial_seq": self._partial_seq,
            "vendor": self.VENDOR,
            "meta": self._extract_meta(message),
        }
        self._publish_event(EVT_ASR_PARTIAL, payload)
        self._publish_event(SM_PARTIAL, payload)
        self._call_callback(self._on_partial, text, latency_ms)

    def _handle_final(self, message: Dict[str, Any]) -> None:
        text = self._extract_text(message)  # may be "", None
        latency_ms = self._extract_latency_ms(message)
        self._maybe_emit_first_token_latency("final")
        meta = self._extract_meta(message) or {}
        if not (isinstance(text, str) and text.strip()):
            # Normalize silent finals into a first-class outcome.
            meta = {**meta, "no_speech": True}
            text = ""

        payload = {
            "text": text,
            "latency_ms": latency_ms,
            "vendor": self.VENDOR,
            "meta": meta,
        }
        self._publish_event(EVT_ASR_FINAL, payload)
        self._publish_event(SM_FINAL, payload)
        self._call_callback(self._on_final, text, latency_ms)

    def _handle_close_ack(self, message: Dict[str, Any], message_type: str) -> None:
        detail: Dict[str, Any] = {
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "vendor_status": message_type,
        }
        ack_seq = message.get("last_seq_no") or message.get("seq_no")
        if isinstance(ack_seq, (int, float)):
            detail["vendor_last_seq_no"] = int(ack_seq)
        self._emit_hub_log("asr.eos.ack", detail)
        if self._close_ack_event.is_set():
            return
        meta = self._extract_meta(message)
        self._publish_event(
            ASR_VENDOR_CLOSE_ACK,
            {"vendor": self.VENDOR, "meta": meta},
        )
        self._close_ack_event.set()

    def _maybe_emit_first_token_latency(
        self, kind: Literal["partial", "final"], *, now_ms: Optional[int] = None
    ) -> None:
        start_ms = self._input_start_ms
        if start_ms is None:
            return
        current_ms: int
        if isinstance(now_ms, (int, float)):
            current_ms = int(now_ms)
        else:
            current_ms = self._now_ms()
        elapsed_ms = max(0, current_ms - int(start_ms))
        detail: Dict[str, Any] = {
            "session_id": self.session_id,
            "turn_id": self.turn_id if isinstance(self.turn_id, str) and self.turn_id else None,
            "elapsed_ms": int(elapsed_ms),
        }
        if kind == "partial":
            if self._first_partial_logged:
                return
            self._first_partial_logged = True
            self._emit_hub_log("asr.first_partial", detail)
            return
        if self._first_final_logged:
            return
        self._first_final_logged = True
        self._emit_hub_log("asr.first_final", detail)

    def _emit_hub_log(self, label: str, detail: Mapping[str, Any]) -> None:
        payload = dict(detail)
        try:
            sanitized = telemetry_bus.redact_payload(payload)
        except Exception:
            sanitized = payload
        self._publish_event(
            EVT_CLIENT_LOG,
            {
                "who": "server",
                "meta": {
                    "label": label,
                    "detail": sanitized,
                },
            },
        )

    async def _wait_for_close_ack(self) -> None:
        try:
            await asyncio.wait_for(
                self._close_ack_event.wait(), timeout=self._close_ack_timeout_s
            )
        except asyncio.TimeoutError:
            self._emit_notice({"kind": "close_ack_timeout"})

    async def _stop_sender(self) -> None:
        self._stop_event.set()
        if not self._pcm_queue.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                while True:
                    item = self._pcm_queue.get_nowait()
                    self._pcm_queue.task_done()
        try:
            self._pcm_queue.put_nowait(self._pcm_sentinel)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                _ = self._pcm_queue.get_nowait()
                self._pcm_queue.task_done()
            with contextlib.suppress(asyncio.QueueFull):
                self._pcm_queue.put_nowait(self._pcm_sentinel)
        sender = self._sender_task
        if sender is not None:
            sender.cancel()
            with contextlib.suppress(Exception):
                await sender

    async def _stop_receiver(self) -> None:
        receiver = self._receiver_task
        if receiver is not None:
            receiver.cancel()
            with contextlib.suppress(Exception):
                await receiver

    async def _stop_ws_ping(self) -> None:
        task = self._ws_ping_task
        if task is not None:
            task.cancel()
            with contextlib.suppress(Exception):
                await task

    async def _finalize_close(self, *, reason: str, publish_close: bool = True) -> None:
        self._stop_event.set()
        self._ready_event.set()
        self._close_ack_event.set()
        async with self._state_lock:
            if self._state == "closed":
                self._closed_event.set()
                return
            self._state = "closed"
        self._close_reason = reason
        if publish_close:
            self._publish_event(
                EVT_ASR_CLOSED,
                {
                    "vendor": self.VENDOR,
                    "meta": {"reason": reason},
                },
            )
        self._call_callback(self._on_closed, reason)
        self._closed_event.set()
        self._ws = None
        self._closing = False

    def _publish_event(self, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
        event = {
            "type": event_type,
            "sid": self._sid,
            "source": self.SOURCE,
            "ts_ms": self._now_ms(),
        }
        if payload:
            event.update(payload)
        try:
            self._bus.publish(event)
        except Exception:  # pragma: no cover - defensive
            _log.exception("telemetry publish failed for %s", event_type)

    def _emit_notice(self, meta: Dict[str, Any]) -> None:
        meta = dict(meta)
        meta.setdefault("vendor", self.VENDOR)
        self._publish_event(SM_NOTICE, {"meta": meta})
        self._call_callback(self._on_notice, meta)

    def _call_callback(self, callback: Callable[..., None], *args) -> None:
        try:
            callback(*args)
        except Exception:  # pragma: no cover - defensive
            _log.exception("speechmatics callback error: %s", getattr(callback, "__name__", callback))

    def _message_type(self, message: Dict[str, Any]) -> str | None:
        for key in ("message", "type", "event"):
            value = message.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    def _extract_text(self, message: Dict[str, Any]) -> str | None:
        candidates: Iterable[Any] = []
        metadata = message.get("metadata")
        if isinstance(metadata, dict):
            candidates = list(metadata.values()) + list(candidates)
            transcript = metadata.get("transcript")
            if isinstance(transcript, str) and transcript.strip():
                return transcript.strip()
        results = message.get("results")
        if isinstance(results, list):
            for result in results:
                if not isinstance(result, dict):
                    continue
                channel = result.get("channel")
                if isinstance(channel, dict):
                    alt = channel.get("alternatives")
                    if isinstance(alt, list):
                        for candidate in alt:
                            if isinstance(candidate, dict):
                                text = candidate.get("transcript") or candidate.get("text")
                                if isinstance(text, str) and text.strip():
                                    return text.strip()
                alternatives = result.get("alternatives")
                if isinstance(alternatives, list):
                    for candidate in alternatives:
                        if isinstance(candidate, dict):
                            text = candidate.get("transcript") or candidate.get("text")
                            if isinstance(text, str) and text.strip():
                                return text.strip()
        for key in ("transcript", "text", "utterance"):
            value = message.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return None

    def _extract_latency_ms(self, message: Dict[str, Any]) -> int:
        metadata = message.get("metadata")
        if isinstance(metadata, dict):
            for key in ("latency_ms", "latency", "duration_ms", "end_time_ms"):
                value = metadata.get(key)
                if isinstance(value, (int, float)):
                    return int(max(0, float(value)))
                if isinstance(value, str):
                    try:
                        return int(max(0, float(value)))
                    except ValueError:
                        continue
            end_time = metadata.get("end_time") or metadata.get("end_time_s")
            if isinstance(end_time, (int, float)):
                return int(max(0, float(end_time) * 1000))
            if isinstance(end_time, str):
                try:
                    return int(max(0, float(end_time) * 1000))
                except ValueError:
                    pass
        return int((time.monotonic() - self._connected_at) * 1000)

    def _extract_meta(self, message: Dict[str, Any]) -> Dict[str, Any]:
        meta = {}
        if isinstance(message.get("metadata"), dict):
            meta.update(message["metadata"])
        session_id = message.get("session_id") or message.get("sessionId")
        if isinstance(session_id, str):
            meta.setdefault("session_id", session_id)
        seq = message.get("seq_no") or message.get("sequence") or message.get("id")
        if isinstance(seq, (int, float)):
            meta.setdefault("seq", int(seq))
        message_type = self._message_type(message)
        if message_type:
            meta.setdefault("message_type", message_type)
        return meta

    async def _send_json(self, payload: Dict[str, Any]) -> None:
        ws = self._ws
        if ws is None:
            raise RuntimeError("websocket not connected")
        data = json.dumps(payload, separators=(",", ":"))
        await ws.send(data)

    async def _send_end_of_stream(self) -> None:
        """Send an EndOfStream message including the last audio sequence number."""

        if self._first_pcm_sent_at is None and self._last_seq_no is None:
            if not self._eos_suppress_logged:
                _log.info(
                    "evt=sm_send_eos_suppressed reason=no_audio_sent last_seq_no=None"
                )
                self._eos_suppress_logged = True
            return

        payload: Dict[str, Any] = {"message": "EndOfStream"}
        if isinstance(self._last_seq_no, int):
            payload["last_seq_no"] = self._last_seq_no
        if not self._eos_logged:
            _log.info("evt=sm_send_eos last_seq_no=%s", payload.get("last_seq_no"))
            self._eos_logged = True
        await self._send_json(payload)

    def _now_ms(self) -> int:
        return int(time.time() * 1000)
