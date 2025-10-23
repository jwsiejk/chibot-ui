"""chat.v2 WebSocket adapter for AskChip."""
from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Iterable, Literal, Optional, Protocol, runtime_checkable
from urllib.parse import parse_qs

import json

from app.telemetry import bus
from app.telemetry.exporter import FileExporter
from app.security.auth import authorize
from app.voice_v2 import (
    EVT_ASR_READY,
    EVT_CHAT_USER,
    EVT_WS_AUDIO_RECV,
    EVT_WS_JSON_RECV,
    EVT_WS_JSON_SEND,
)
from app.ws.validator import validate_frame

CHAT_V2_SUBPROTOCOL = "chat.v2"
TEXT_FRAME_LIMIT_BYTES = 64 * 1024
BINARY_FRAME_LIMIT_BYTES = 2 * 1024 * 1024
PING_MIN_INTERVAL_MS = 500
RATE_LIMIT_CAPACITY = 25
RATE_LIMIT_WINDOW_SECONDS = 2.0
RATE_LIMIT_CLOSE_CODE = 1013
_AUDIO_VIOLATION_LIMIT = 3

AUDIO_SEQ_WINDOW = 8

QUEUE_ON_THRESHOLD = 12
QUEUE_OFF_THRESHOLD = 6

_DEFAULT_WS_PING_INTERVAL_MS = 25_000

EVT_BACKPRESSURE_ON = "EVT_BACKPRESSURE_ON"
EVT_BACKPRESSURE_OFF = "EVT_BACKPRESSURE_OFF"

EVT_AUTH_DENIED = "EVT_AUTH_DENIED"
EVT_RATE_LIMIT = "EVT_RATE_LIMIT"

EVT_WS_OUTBOX_DROP = "EVT_WS_OUTBOX_DROP"

_OUTBOUND_ALLOWED_TYPES = {
    "policy.interaction",
    "info",
    "tts.start",
    "tts.end",
    "asr.ready",
    "asr.partial",
    "asr.final",
    "error",
    "chat.message",
    "chat.history",
    "dialog.plan",
}

_OUTBOX_MAXSIZE = 256

_RESUME_TTL_MS = 10_000
_RESUME_MARKER_TYPES = {"tts.start", "tts.end", "asr.final"}
_RESUME_MARKER_LIMIT = 10

_POLICY_STABLE_KEYS = ("mode", "allow_auto_vad", "barge_in_enabled")

_logger = logging.getLogger(__name__)

_ALLOWED_TEXT_FRAME_TYPES = {
    "client.ready",
    "audio.header",
    "admin.toggle",
    "chat.user",
}


class TokenBucket:
    """Simple in-memory token bucket."""

    def __init__(self, capacity: int, refill_seconds: float) -> None:
        self.capacity = capacity
        self.tokens = float(capacity)
        self.refill_seconds = refill_seconds
        self.last_refill = time.monotonic()

    def consume(self, count: int, now: Optional[float] = None) -> bool:
        if now is None:
            now = time.monotonic()
        elapsed = max(0.0, now - self.last_refill)
        if elapsed > 0:
            refill = (self.capacity / self.refill_seconds) * elapsed
            if refill > 0:
                self.tokens = min(self.capacity, self.tokens + refill)
                self.last_refill = now
        if self.tokens < count:
            return False
        self.tokens -= count
        return True


class _PartialCoalescer:
    """Rate limits outbound ASR partial frames for a connection."""

    def __init__(self, min_interval_ms: int = 50) -> None:
        self.min_interval_ms = max(0, int(min_interval_ms))
        self._last_emit_ms = 0
        self._pending: Optional[Dict[str, Any]] = None
        self._timer: Optional[asyncio.TimerHandle] = None

    def offer(
        self,
        payload: Dict[str, Any],
        *,
        now_ms: int,
        loop: asyncio.AbstractEventLoop,
        emit: Callable[[Dict[str, Any]], None],
    ) -> None:
        if self.min_interval_ms <= 0:
            self._pending = None
            self._cancel_timer()
            self._last_emit_ms = now_ms
            emit(payload)
            return

        if self._pending is None and now_ms - self._last_emit_ms >= self.min_interval_ms:
            self._last_emit_ms = now_ms
            emit(payload)
            return

        self._pending = dict(payload)
        if self._timer is None:
            delay_ms = max(0, self.min_interval_ms - (now_ms - self._last_emit_ms))
            self._timer = loop.call_later(
                delay_ms / 1000.0,
                self._flush,
                loop,
                emit,
            )

    def cancel(self) -> None:
        self._pending = None
        self._cancel_timer()

    def _flush(self, loop: asyncio.AbstractEventLoop, emit: Callable[[Dict[str, Any]], None]) -> None:
        self._timer = None
        pending = self._pending
        if pending is None:
            return
        self._pending = None
        now_ms = int(time.time() * 1000)
        self._last_emit_ms = now_ms
        emit(pending)

    def _cancel_timer(self) -> None:
        timer = self._timer
        self._timer = None
        if timer is not None:
            timer.cancel()


@runtime_checkable
class EngineHooks(Protocol):
    """Engine surface used by the WebSocket adapter."""

    def on_open(self, sid: str, headers: Dict[str, str]) -> Any:  # pragma: no cover - signature stub
        """Called when a new WebSocket connection is established."""

    def on_json(self, sid: str, frame: Dict[str, Any]) -> Any:  # pragma: no cover - signature stub
        """Handle a validated JSON frame from the client."""

    def on_audio(self, sid: str, chunk: bytes, seq: int) -> Any:  # pragma: no cover - signature stub
        """Handle a binary audio chunk from the client."""

    def on_close(self, sid: str, code: int, reason: Optional[str]) -> Any:  # pragma: no cover - signature stub
        """Handle the WebSocket closing."""


@dataclass
class AdapterContext:
    """Per-connection state."""

    sid: str
    headers: Dict[str, str]
    audio_seq: int = 0
    audio_expected_seq: int = 0
    audio_highest_seq: int = -1
    audio_buffer: Dict[int, bytes] = field(default_factory=dict)
    audio_window: int = AUDIO_SEQ_WINDOW
    last_pong_sent_ms: int = 0
    ip: Optional[str] = None
    sid_bucket: Optional[TokenBucket] = None
    ip_bucket: Optional[TokenBucket] = None
    audio_profile: Optional[Dict[str, Any]] = None
    accepting_audio: bool = True
    audio_violation_count: int = 0
    outbound_queue_depth: int = 0
    backpressure_state: Literal["off", "on"] = "off"
    outbox: asyncio.Queue[Dict[str, Any]] | None = None
    outbound_task: asyncio.Task[None] | None = None
    subscription_token: Optional[str] = None
    asr_ready: bool = False
    asr_subscription_token: Optional[str] = None
    server_keepalive_task: asyncio.Task[None] | None = None
    last_policy_interaction: Optional[Dict[str, Any]] = None
    resume_token: Optional[str] = None
    resume_expiry_ms: int = 0
    recent_markers: list[Dict[str, Any]] = field(default_factory=list)
    partial_seq: int = 0
    partial_coalescer: _PartialCoalescer = field(default_factory=_PartialCoalescer)


@dataclass
class _ResumeState:
    sid: str
    expiry_ms: int
    markers: list[Dict[str, Any]]


class ChatV2Adapter:
    """Minimal chat.v2 WebSocket adapter with telemetry taps."""

    def __init__(
        self,
        engine: EngineHooks | None = None,
        exporter: FileExporter | None = None,
        *,
        text_limit_bytes: int = TEXT_FRAME_LIMIT_BYTES,
        binary_limit_bytes: int = BINARY_FRAME_LIMIT_BYTES,
    ) -> None:
        self.engine = engine
        self.exporter = exporter
        self.text_limit_bytes = text_limit_bytes
        self.binary_limit_bytes = binary_limit_bytes
        self._ip_buckets: Dict[str, TokenBucket] = {}
        self._contexts: Dict[str, AdapterContext] = {}
        self._resume_tokens: Dict[str, _ResumeState] = {}
        self._ping_interval_ms = max(
            0,
            int(os.getenv("WS_PING_INTERVAL_MS", str(_DEFAULT_WS_PING_INTERVAL_MS))),
        )

    async def __call__(self, scope: dict, receive: Callable[[], Awaitable[dict]], send: Callable[[dict], Awaitable[None]]) -> None:
        if scope.get("type") != "websocket":
            raise RuntimeError("ChatV2Adapter can only handle websocket scopes")

        connect_event = await receive()
        if connect_event.get("type") != "websocket.connect":
            return

        subprotocols = scope.get("subprotocols") or []
        if CHAT_V2_SUBPROTOCOL not in subprotocols:
            await self._reject_subprotocol(send)
            return

        self._purge_expired_resume_tokens()

        resume_token_param, resume_error = self._extract_resume_token(scope)
        resume_state: Optional[_ResumeState] = None
        resume_replay: list[Dict[str, Any]] = []

        sid = uuid.uuid4().hex
        if resume_error is None and resume_token_param:
            resume_state = self._consume_resume_token(resume_token_param)
            if resume_state is None:
                resume_error = "resume_invalid"
            else:
                sid = resume_state.sid
                resume_replay = [self._clone_frame(marker) for marker in resume_state.markers]

        headers = self._decode_headers(scope.get("headers", ()))
        auth_headers, detail = self._prepare_authorization_headers(scope, headers)
        if detail:
            await self._publish(
                EVT_AUTH_DENIED,
                sid,
                {"reason": detail},
            )
            await self._deny_http(send, detail)
            return

        allowed, reason = authorize(auth_headers)
        if not allowed:
            await self._publish(
                EVT_AUTH_DENIED,
                sid,
                {"reason": reason or "missing or invalid auth"},
            )
            await self._deny_http(send, "missing or invalid auth")
            return

        await send({"type": "websocket.accept", "subprotocol": CHAT_V2_SUBPROTOCOL})

        if resume_error is not None:
            await self._send_json(
                send,
                sid,
                {
                    "type": "error",
                    "code": "resume_invalid",
                    "detail": "Resume token expired or invalid",
                    "retryable": False,
                },
            )
            await send({"type": "websocket.close", "code": 1008, "reason": "resume_invalid"})
            return

        ctx = AdapterContext(sid=sid, headers=auth_headers)
        ctx.sid_bucket = TokenBucket(RATE_LIMIT_CAPACITY, RATE_LIMIT_WINDOW_SECONDS)
        client = scope.get("client")
        ctx.ip = client[0] if isinstance(client, tuple) and client else None
        if ctx.ip:
            ctx.ip_bucket = self._ip_buckets.setdefault(
                ctx.ip,
                TokenBucket(RATE_LIMIT_CAPACITY, RATE_LIMIT_WINDOW_SECONDS),
            )

        self._contexts[ctx.sid] = ctx
        self._start_asr_ready_tracker(ctx)
        self._start_outbound_bridge(ctx, send)
        self._start_server_keepalive(ctx, send)

        if resume_replay:
            ctx.recent_markers = [self._clone_frame(marker) for marker in resume_replay]

        if self.exporter:
            self.exporter.begin(ctx.sid)
        await self._invoke_engine("on_open", ctx.sid, auth_headers)

        for marker in resume_replay:
            await self._send_json(send, ctx.sid, marker)

        close_code = 1000
        close_reason: Optional[str] = None

        try:
            while True:
                message = await receive()
                msg_type = message.get("type")

                if msg_type == "websocket.receive":
                    if message.get("text") is not None:
                        result = await self._handle_text(message["text"], ctx, send)
                        if not result.should_continue:
                            close_code = result.close_code or 1000
                            close_reason = result.close_reason
                            await send(
                                {
                                    "type": "websocket.close",
                                    "code": close_code,
                                    "reason": close_reason,
                                }
                            )
                            break
                    elif message.get("bytes") is not None:
                        result = await self._handle_binary(message["bytes"], ctx, send)
                        if not result.should_continue:
                            close_code = result.close_code or 1000
                            close_reason = result.close_reason
                            await send(
                                {
                                    "type": "websocket.close",
                                    "code": close_code,
                                    "reason": close_reason,
                                }
                            )
                            break
                elif msg_type == "websocket.disconnect":
                    close_code = message.get("code", 1000)
                    close_reason = message.get("reason")
                    break
        except Exception:  # pragma: no cover - defensive guard
            close_code = 1011
            close_reason = "internal_error"
            await send({"type": "websocket.close", "code": close_code, "reason": close_reason})
            raise
        finally:
            await self._stop_server_keepalive(ctx)
            await self._cleanup_outbound(ctx)
            self._stop_asr_ready_tracker(ctx)
            await self._invoke_engine("on_close", ctx.sid, close_code, close_reason)
            if self.exporter:
                self.exporter.end(ctx.sid, {"close_code": close_code})
            self._contexts.pop(ctx.sid, None)

    def set_accepting_audio(self, sid: str, accepting: bool) -> None:
        """Toggle whether the given connection should accept audio frames."""

        ctx = self._contexts.get(sid)
        if ctx is not None:
            ctx.accepting_audio = accepting
            if accepting:
                ctx.audio_violation_count = 0

    def get_context(self, sid: str) -> Optional[AdapterContext]:
        """Return the adapter context for testing hooks."""

        return self._contexts.get(sid)

    async def _reject_subprotocol(self, send: Callable[[dict], Awaitable[None]]) -> None:
        body = json.dumps(
            {
                "type": "error",
                "code": "bad_subprotocol",
                "detail": "use chat.v2",
            }
        ).encode("utf-8")
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ]
        await send({"type": "websocket.http.response.start", "status": 426, "headers": headers})
        await send({"type": "websocket.http.response.body", "body": body, "more_body": False})

    @dataclass
    class _HandleResult:
        should_continue: bool
        close_code: Optional[int] = None
        close_reason: Optional[str] = None

    async def _handle_text(
        self, data: str, ctx: AdapterContext, send: Callable[[dict], Awaitable[None]]
    ) -> _HandleResult:
        limited = await self._check_rate_limit(ctx, send)
        if limited is not None:
            return limited

        try:
            payload_bytes = data.encode("utf-8")
            byte_count = len(payload_bytes)
        except UnicodeEncodeError:
            sanitized = data.encode("utf-8", "replace")
            meta = {
                "byte_count": len(sanitized),
                "error": "bad_utf8",
                "frame_type": None,
                "ws": {
                    "dir": "in",
                    "size": len(sanitized),
                    "preview": self._make_preview_from_bytes(sanitized),
                },
            }
            await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta)
            await self._send_error(send, ctx.sid, "bad_utf8", "Text frame must be UTF-8 encoded JSON")
            return self._HandleResult(True)

        meta: Dict[str, Any] = {
            "byte_count": byte_count,
            "frame_type": None,
            "ws": {
                "dir": "in",
                "size": byte_count,
            },
        }

        preview = self._make_preview_from_bytes(payload_bytes)
        if preview is not None:
            meta["ws"]["preview"] = preview

        if byte_count > self.text_limit_bytes:
            meta["error"] = "frame_too_large"
            await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta)
            await self._send_error(send, ctx.sid, "frame_too_large", "Text frame exceeds limit")
            return self._HandleResult(False, 1009, "frame_too_large")

        try:
            frame = json.loads(payload_bytes.decode("utf-8"))
        except json.JSONDecodeError as exc:
            meta["error"] = "bad_json"
            await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta)
            await self._send_error(send, ctx.sid, "bad_json", f"Invalid JSON payload: {exc.msg}")
            return self._HandleResult(True)

        if not isinstance(frame, dict):
            meta["error"] = "schema_invalid"
            await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta)
            await self._send_error(send, ctx.sid, "schema_invalid", "Frame must be a JSON object")
            return self._HandleResult(True)

        raw_type = frame.get("type")
        if not isinstance(raw_type, str):
            meta["error"] = "schema_invalid"
            await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta)
            await self._send_error(send, ctx.sid, "schema_invalid", "Frame missing type field")
            return self._HandleResult(True)

        frame_type = raw_type
        meta["frame_type"] = frame_type

        if frame_type == "ping":
            await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta)
            now_ms = int(time.time() * 1000)
            if now_ms - ctx.last_pong_sent_ms >= PING_MIN_INTERVAL_MS:
                ctx.last_pong_sent_ms = now_ms
                reply_ts = frame.get("t")
                if not isinstance(reply_ts, int):
                    reply_ts = now_ms
                await self._send_json(send, ctx.sid, {"type": "pong", "t": reply_ts})
            return self._HandleResult(True)

        if frame_type == "chat.user":
            text = frame.get("text")
            if not isinstance(text, str):
                meta["error"] = "schema_invalid"
                await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta)
                await self._send_error(
                    send,
                    ctx.sid,
                    "schema_invalid",
                    "chat.user requires a string text field",
                )
                return self._HandleResult(False, 1003, "schema_invalid")

            try:
                text_bytes = text.encode("utf-8")
            except UnicodeEncodeError:
                meta["error"] = "bad_utf8"
                await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta)
                await self._send_error(
                    send,
                    ctx.sid,
                    "bad_utf8",
                    "chat.user text must be valid UTF-8",
                )
                return self._HandleResult(False, 1007, "bad_utf8")

            client_msg_id = frame.get("client_msg_id")
            if client_msg_id is not None and not isinstance(client_msg_id, str):
                meta["error"] = "schema_invalid"
                await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta)
                await self._send_error(
                    send,
                    ctx.sid,
                    "schema_invalid",
                    "chat.user client_msg_id must be a string if provided",
                )
                return self._HandleResult(False, 1003, "schema_invalid")

            ws_meta = meta.get("ws")
            event_meta: Dict[str, Any] = {}
            if isinstance(ws_meta, dict):
                event_meta["ws"] = dict(ws_meta)
            event_meta["text_length"] = len(text_bytes)
            preview = self._make_preview_from_bytes(text_bytes)
            if preview is not None:
                event_meta["preview"] = preview
            if client_msg_id is not None:
                event_meta["client_msg_id"] = client_msg_id

            event = {
                "schema_version": "1",
                "type": EVT_CHAT_USER,
                "sid": ctx.sid,
                "who": "server",
                "source": "ws_server",
                "meta": event_meta,
                "text": text,
            }
            if client_msg_id is not None:
                event["client_msg_id"] = client_msg_id
            bus.publish(event)
        else:
            if frame_type not in _ALLOWED_TEXT_FRAME_TYPES:
                meta["error"] = "unknown_type"
                await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta)
                await self._send_error(send, ctx.sid, "unknown_type", frame_type)
                return self._HandleResult(True)

            is_valid, hint = validate_frame(frame)
            if not is_valid:
                meta["error"] = "schema_invalid"
                await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta)
                detail = hint or "Frame failed validation"
                await self._send_error(send, ctx.sid, "schema_invalid", detail)
                return self._HandleResult(True)

        if frame_type == "audio.header":
            profile = {
                "format": frame.get("format"),
                "sample_rate": frame.get("sample_rate"),
                "channels": frame.get("channels"),
            }
            seq_start = frame.get("seq_start")
            if seq_start is not None:
                if not isinstance(seq_start, int):
                    meta["error"] = "schema_invalid"
                    await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta)
                    await self._send_error(
                        send,
                        ctx.sid,
                        "schema_invalid",
                        "audio.header seq_start must be an integer",
                    )
                    return self._HandleResult(False, 1003, "schema_invalid")
                if seq_start < 0:
                    meta["error"] = "schema_invalid"
                    await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta)
                    await self._send_error(
                        send,
                        ctx.sid,
                        "schema_invalid",
                        "audio.header seq_start must be >= 0",
                    )
                    return self._HandleResult(False, 1003, "schema_invalid")
                profile["seq_start"] = seq_start
            if ctx.audio_profile is not None:
                meta["error"] = "schema_invalid"
                await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta)
                await self._send_error(
                    send,
                    ctx.sid,
                    "schema_invalid",
                    "duplicate or conflicting audio.header",
                )
                return self._HandleResult(True)
            ctx.audio_profile = profile
            if seq_start is not None:
                ctx.audio_seq = max(0, seq_start)
                ctx.audio_expected_seq = ctx.audio_seq
                ctx.audio_highest_seq = ctx.audio_seq - 1
                ctx.audio_buffer.clear()

        await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta)
        await self._invoke_engine("on_json", ctx.sid, frame)
        return self._HandleResult(True)

    async def _handle_binary(
        self, data: bytes, ctx: AdapterContext, send: Callable[[dict], Awaitable[None]]
    ) -> _HandleResult:
        byte_count = len(data)

        limited = await self._check_rate_limit(ctx, send)
        if limited is not None:
            return limited

        if byte_count > self.binary_limit_bytes:
            await self._publish(
                EVT_WS_AUDIO_RECV,
                ctx.sid,
                {
                    "byte_count": byte_count,
                    "error": "frame_too_large",
                    "ws": {"dir": "in", "size": byte_count},
                },
            )
            await self._send_error(send, ctx.sid, "frame_too_large", "Binary frame exceeds limit")
            return self._HandleResult(False, 1009, "frame_too_large")

        if not ctx.asr_ready:
            meta = {
                "byte_count": byte_count,
                "error": "audio_not_expected",
                "ws": {"dir": "in", "size": byte_count},
            }
            await self._publish(EVT_WS_AUDIO_RECV, ctx.sid, meta)
            await self._send_error(
                send,
                ctx.sid,
                "audio_not_expected",
                "asr not ready",
            )
            return self._HandleResult(False, 1003, "audio_not_expected")

        if not ctx.accepting_audio:
            ctx.audio_violation_count += 1
            violation_meta = {
                "byte_count": byte_count,
                "error": "audio_not_expected_close"
                if ctx.audio_violation_count >= _AUDIO_VIOLATION_LIMIT
                else "audio_not_expected",
                "ws": {"dir": "in", "size": byte_count},
                "violations": ctx.audio_violation_count,
            }
            await self._publish(EVT_WS_AUDIO_RECV, ctx.sid, violation_meta)
            await self._send_error(send, ctx.sid, "audio_not_expected", "engine not accepting audio")
            if ctx.audio_violation_count >= _AUDIO_VIOLATION_LIMIT:
                return self._HandleResult(False, 1003, "audio_not_expected")
            return self._HandleResult(True)

        ctx.audio_violation_count = 0
        if ctx.audio_highest_seq < 0:
            ctx.audio_expected_seq = ctx.audio_seq
        seq = ctx.audio_seq
        ctx.audio_seq += 1
        await self._ingest_audio_chunk(ctx, bytes(data), seq)
        return self._HandleResult(True)

    async def _check_rate_limit(
        self, ctx: AdapterContext, send: Callable[[dict], Awaitable[None]]
    ) -> Optional[_HandleResult]:
        if ctx.sid_bucket is None:
            return None

        now = time.monotonic()
        if not ctx.sid_bucket.consume(1, now):
            return await self._handle_rate_limit(ctx, send, "sid")

        if ctx.ip_bucket is not None and not ctx.ip_bucket.consume(1, now):
            return await self._handle_rate_limit(ctx, send, "ip")

        return None

    async def _handle_rate_limit(
        self, ctx: AdapterContext, send: Callable[[dict], Awaitable[None]], scope: str
    ) -> "ChatV2Adapter._HandleResult":
        await self._publish(
            EVT_RATE_LIMIT,
            ctx.sid,
            {"scope": scope, "ip": ctx.ip},
        )
        await self._send_error(send, ctx.sid, "rate_limited", "try later")
        return self._HandleResult(False, RATE_LIMIT_CLOSE_CODE, "rate_limited")

    async def _ingest_audio_chunk(self, ctx: AdapterContext, chunk: bytes, seq: int) -> None:
        if seq < 0:
            return

        window = max(1, ctx.audio_window)
        expected = ctx.audio_expected_seq

        if seq < expected - window:
            return
        if seq < expected:
            return
        if seq in ctx.audio_buffer:
            return

        ctx.audio_buffer[seq] = bytes(chunk)
        if seq > ctx.audio_highest_seq:
            ctx.audio_highest_seq = seq

        gap = self._compute_audio_gap(ctx)
        if gap is not None:
            gap_from, gap_to = gap
            await self._publish(
                "EVT_AUDIO_GAP",
                ctx.sid,
                {"from_seq": gap_from, "to_seq": gap_to},
            )

        await self._flush_audio_buffer(ctx)

    def _compute_audio_gap(self, ctx: AdapterContext) -> Optional[tuple[int, int]]:
        expected = ctx.audio_expected_seq
        if expected in ctx.audio_buffer:
            return None
        if not ctx.audio_buffer:
            return None
        if ctx.audio_highest_seq - expected < ctx.audio_window:
            return None

        next_candidates = [seq for seq in ctx.audio_buffer if seq >= expected]
        if not next_candidates:
            return None

        next_available = min(next_candidates)
        if next_available <= expected:
            return None

        gap_from = expected
        self._drop_buffer_before(ctx, next_available)
        ctx.audio_expected_seq = next_available
        return gap_from, next_available

    async def _flush_audio_buffer(self, ctx: AdapterContext) -> None:
        while True:
            seq = ctx.audio_expected_seq
            chunk = ctx.audio_buffer.pop(seq, None)
            if chunk is None:
                break
            await self._emit_audio_chunk(ctx, chunk, seq)
            ctx.audio_expected_seq += 1
        if ctx.audio_buffer:
            ctx.audio_highest_seq = max(ctx.audio_buffer)
        else:
            ctx.audio_highest_seq = ctx.audio_expected_seq - 1

    async def _emit_audio_chunk(self, ctx: AdapterContext, chunk: bytes, seq: int) -> None:
        byte_count = len(chunk)
        meta = {
            "byte_count": byte_count,
            "seq": seq,
            "ws": {"dir": "in", "size": byte_count},
        }
        await self._publish(EVT_WS_AUDIO_RECV, ctx.sid, meta)
        await self._invoke_engine("on_audio", ctx.sid, chunk, seq)

    def _drop_buffer_before(self, ctx: AdapterContext, threshold: int) -> None:
        removed = False
        for key in list(ctx.audio_buffer):
            if key < threshold:
                ctx.audio_buffer.pop(key, None)
                removed = True
        if removed:
            if ctx.audio_buffer:
                ctx.audio_highest_seq = max(ctx.audio_buffer)
            else:
                ctx.audio_highest_seq = ctx.audio_expected_seq - 1

    async def _send_json(self, send: Callable[[dict], Awaitable[None]], sid: str, payload: Dict[str, Any]) -> None:
        text = json.dumps(payload, separators=(",", ":"))
        await send({"type": "websocket.send", "text": text})
        payload_bytes = text.encode("utf-8")
        byte_count = len(payload_bytes)
        meta: Dict[str, Any] = {
            "byte_count": byte_count,
            "frame_type": payload.get("type"),
            "ws": {
                "dir": "out",
                "size": byte_count,
            },
        }
        preview = self._make_preview_from_bytes(payload_bytes)
        if preview is not None:
            meta["ws"]["preview"] = preview
        await self._publish(EVT_WS_JSON_SEND, sid, meta)

    async def _send_error(
        self, send: Callable[[dict], Awaitable[None]], sid: str, code: str, detail: str
    ) -> None:
        payload = {"type": "error", "code": code, "detail": detail}
        await self._send_json(send, sid, payload)

    def _start_server_keepalive(
        self, ctx: AdapterContext, send: Callable[[dict], Awaitable[None]]
    ) -> None:
        if self._ping_interval_ms <= 0 or ctx.server_keepalive_task is not None:
            return

        async def _run() -> None:
            interval = self._ping_interval_ms / 1000.0
            if interval <= 0:
                return
            try:
                while True:
                    await asyncio.sleep(interval)
                    payload = json.dumps(
                        {"type": "keepalive", "ts": int(time.time() * 1000)},
                        separators=(",", ":"),
                    )
                    await send({"type": "websocket.send", "text": payload})
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - defensive
                _logger.exception("Failed to send keepalive for sid %s", ctx.sid)
            finally:
                ctx.server_keepalive_task = None

        ctx.server_keepalive_task = asyncio.create_task(_run())

    async def _stop_server_keepalive(self, ctx: AdapterContext) -> None:
        task = ctx.server_keepalive_task
        ctx.server_keepalive_task = None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    def _start_outbound_bridge(
        self, ctx: AdapterContext, send: Callable[[dict], Awaitable[None]]
    ) -> None:
        loop = asyncio.get_running_loop()
        ctx.outbox = asyncio.Queue(maxsize=_OUTBOX_MAXSIZE)

        def _queue_payload(payload: Dict[str, Any], *, clone: bool = True) -> None:
            if ctx.outbox is None:
                return
            item = dict(payload) if clone else payload
            try:
                ctx.outbox.put_nowait(item)
            except asyncio.QueueFull:
                now = ctx.outbox.qsize()
                try:
                    asyncio.create_task(
                        self._publish(
                            EVT_WS_OUTBOX_DROP,
                            ctx.sid,
                            {"sid": ctx.sid, "dropped": 1, "now": now},
                        )
                    )
                except RuntimeError:
                    pass

        def _enqueue(payload: Dict[str, Any]) -> None:
            if payload.get("type") == "asr.partial":
                self._offer_partial_frame(
                    ctx,
                    loop,
                    payload,
                    lambda frame: _queue_payload(frame, clone=False),
                )
                return
            _queue_payload(payload)

        def _handle_event(event: dict) -> None:
            if event.get("sid") != ctx.sid or ctx.outbox is None:
                return
            payload = self._extract_outbound_payload(ctx, event)
            if payload is None:
                return

            def _on_loop() -> None:
                _enqueue(payload)

            try:
                loop.call_soon_threadsafe(_on_loop)
            except RuntimeError:
                pass

        ctx.subscription_token = bus.subscribe(EVT_WS_JSON_SEND, _handle_event)
        ctx.outbound_task = asyncio.create_task(self._run_outbound_sender(ctx, send))

    def _offer_partial_frame(
        self,
        ctx: AdapterContext,
        loop: asyncio.AbstractEventLoop,
        payload: Dict[str, Any],
        enqueue: Callable[[Dict[str, Any]], None],
    ) -> None:
        def _emit(frame: Dict[str, Any]) -> None:
            prepared = self._prepare_partial_frame(ctx, frame)
            enqueue(prepared)

        ctx.partial_coalescer.offer(
            payload,
            now_ms=self._now_ms(),
            loop=loop,
            emit=_emit,
        )

    def _prepare_partial_frame(self, ctx: AdapterContext, payload: Dict[str, Any]) -> Dict[str, Any]:
        prepared = dict(payload)
        seq_value = prepared.get("partial_seq")
        if isinstance(seq_value, int) and seq_value > ctx.partial_seq:
            ctx.partial_seq = seq_value
        else:
            ctx.partial_seq += 1
            prepared["partial_seq"] = ctx.partial_seq
        return prepared

    def _start_asr_ready_tracker(self, ctx: AdapterContext) -> None:
        if ctx.asr_subscription_token is not None:
            return

        def _handle(event: dict) -> None:
            if event.get("type") != EVT_ASR_READY:
                return
            if event.get("sid") != ctx.sid:
                return
            ctx.asr_ready = True

        ctx.asr_subscription_token = bus.subscribe(EVT_ASR_READY, _handle)

    def _extract_outbound_payload(
        self, ctx: AdapterContext, event: dict
    ) -> Optional[Dict[str, Any]]:
        payload = self._coerce_payload(event.get("payload"))
        if payload is None:
            meta = event.get("meta")
            if isinstance(meta, dict):
                ws_meta = meta.get("ws")
                if isinstance(ws_meta, dict):
                    frame = ws_meta.get("frame")
                    if frame is None:
                        frame = ws_meta.get("preview")
                    payload = self._coerce_payload(frame)

        if payload is None:
            return None

        frame_type = payload.get("type") if isinstance(payload, dict) else None
        if not isinstance(frame_type, str) or frame_type not in _OUTBOUND_ALLOWED_TYPES:
            return None
        normalized = dict(payload)
        if frame_type == "policy.interaction":
            normalized = self._sanitize_policy_interaction(normalized)
            last_policy = ctx.last_policy_interaction
            if last_policy is not None and last_policy == normalized:
                return None
            ctx.last_policy_interaction = json.loads(
                json.dumps(normalized, separators=(",", ":"))
            )
        if frame_type == "info":
            self._ensure_resume_token(ctx)
            if ctx.resume_token:
                normalized["resume_token"] = ctx.resume_token
                normalized["resume_ttl_ms"] = _RESUME_TTL_MS
        if frame_type in _RESUME_MARKER_TYPES:
            self._record_resume_marker(ctx, normalized)
        return normalized

    @staticmethod
    def _sanitize_policy_interaction(frame: Dict[str, Any]) -> Dict[str, Any]:
        sanitized = {key: value for key, value in frame.items() if key != "policy"}
        if "policy" in frame:
            policy = frame.get("policy")
            if isinstance(policy, dict):
                sanitized_policy = {
                    key: policy[key]
                    for key in _POLICY_STABLE_KEYS
                    if key in policy
                }
            else:
                sanitized_policy = {}
            sanitized["policy"] = sanitized_policy
        return sanitized

    @staticmethod
    def _coerce_payload(raw: Any) -> Optional[Dict[str, Any]]:
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                decoded = json.loads(raw)
            except json.JSONDecodeError:
                return None
            if isinstance(decoded, dict):
                return decoded
        return None

    async def _run_outbound_sender(
        self, ctx: AdapterContext, send: Callable[[dict], Awaitable[None]]
    ) -> None:
        queue = ctx.outbox
        if queue is None:
            return
        try:
            while True:
                payload = await queue.get()
                try:
                    await self._send_outbound_frame(send, payload)
                except asyncio.CancelledError:
                    raise
                except Exception:  # pragma: no cover - defensive
                    _logger.exception("Failed to deliver outbound frame for sid %s", ctx.sid)
                finally:
                    queue.task_done()
        except asyncio.CancelledError:
            raise
        finally:
            ctx.outbox = None

    async def _send_outbound_frame(
        self, send: Callable[[dict], Awaitable[None]], payload: Dict[str, Any]
    ) -> None:
        text = json.dumps(payload, separators=(",", ":"))
        await send({"type": "websocket.send", "text": text})

    async def _cleanup_outbound(self, ctx: AdapterContext) -> None:
        token = ctx.subscription_token
        ctx.subscription_token = None
        if token:
            bus.unsubscribe(token)

        task = ctx.outbound_task
        ctx.outbound_task = None
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        ctx.partial_coalescer.cancel()

        ctx.outbox = None

    def _stop_asr_ready_tracker(self, ctx: AdapterContext) -> None:
        token = ctx.asr_subscription_token
        ctx.asr_subscription_token = None
        if token:
            bus.unsubscribe(token)
        ctx.asr_ready = False

    async def set_outbound_queue_depth(self, sid: str, queued: int) -> None:
        """Record the estimated outbound queue depth and emit diagnostics if needed."""

        ctx = self._contexts.get(sid)
        if ctx is None:
            return

        if not isinstance(queued, int):
            try:
                queued = int(queued)
            except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
                raise TypeError("queued must be an integer") from exc

        await self._update_backpressure(ctx, max(0, queued))

    async def _publish(self, event_type: str, sid: str, meta: Dict[str, Any]) -> None:
        event = {
            "schema_version": "1",
            "type": event_type,
            "sid": sid,
            "who": "server",
            "source": "ws_server",
            "meta": dict(meta),
        }
        bus.publish(event)

    async def _deny_http(self, send: Callable[[dict], Awaitable[None]], detail: str) -> None:
        body = json.dumps({"type": "error", "code": "unauthorized", "detail": detail}).encode("utf-8")
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ]
        await send({"type": "websocket.http.response.start", "status": 401, "headers": headers})
        await send({"type": "websocket.http.response.body", "body": body, "more_body": False})

    async def _invoke_engine(self, hook: str, *args: Any) -> None:
        if not self.engine:
            return
        handler = getattr(self.engine, hook, None)
        if handler is None:
            return
        result = handler(*args)
        if inspect.isawaitable(result):
            await result

    @staticmethod
    def _decode_headers(headers: Iterable[tuple[bytes, bytes]]) -> Dict[str, str]:
        decoded: Dict[str, str] = {}
        for key, value in headers:
            decoded[key.decode("latin1").lower()] = value.decode("latin1")
        return decoded

    def _purge_expired_resume_tokens(self) -> None:
        now = self._now_ms()
        for token, state in list(self._resume_tokens.items()):
            if state.expiry_ms < now:
                self._resume_tokens.pop(token, None)

    def _extract_resume_token(self, scope: dict) -> tuple[Optional[str], Optional[str]]:
        raw_query = scope.get("query_string", b"")
        if not raw_query:
            return None, None

        if isinstance(raw_query, bytes):
            try:
                query = raw_query.decode("utf-8")
            except UnicodeDecodeError:
                return None, "resume_invalid"
        else:
            query = str(raw_query)

        params = parse_qs(query, keep_blank_values=True)
        values = params.get("resume")
        if not values:
            return None, None
        if len(values) != 1:
            return None, "resume_invalid"

        token = values[0]
        if not token:
            return None, "resume_invalid"

        return token, None

    def _consume_resume_token(self, token: str) -> Optional[_ResumeState]:
        state = self._resume_tokens.get(token)
        if state is None:
            return None
        now = self._now_ms()
        if state.expiry_ms < now:
            self._resume_tokens.pop(token, None)
            return None
        return self._resume_tokens.pop(token)

    def _ensure_resume_token(self, ctx: AdapterContext) -> None:
        self._purge_expired_resume_tokens()
        token = ctx.resume_token
        if token is not None and token in self._resume_tokens:
            state = self._resume_tokens[token]
            state.markers = [self._clone_frame(marker) for marker in ctx.recent_markers]
            return

        token = uuid.uuid4().hex
        expiry = self._now_ms() + _RESUME_TTL_MS
        ctx.resume_token = token
        ctx.resume_expiry_ms = expiry
        self._resume_tokens[token] = _ResumeState(
            sid=ctx.sid,
            expiry_ms=expiry,
            markers=[self._clone_frame(marker) for marker in ctx.recent_markers],
        )

    def _record_resume_marker(self, ctx: AdapterContext, payload: Dict[str, Any]) -> None:
        clone = self._clone_frame(payload)
        ctx.recent_markers.append(clone)
        if len(ctx.recent_markers) > _RESUME_MARKER_LIMIT:
            ctx.recent_markers = ctx.recent_markers[-_RESUME_MARKER_LIMIT :]

        token = ctx.resume_token
        if token:
            state = self._resume_tokens.get(token)
            if state:
                state.markers = [self._clone_frame(marker) for marker in ctx.recent_markers]

    @staticmethod
    def _clone_frame(payload: Dict[str, Any]) -> Dict[str, Any]:
        return json.loads(json.dumps(payload, separators=(",", ":")))

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    @staticmethod
    def _extract_browser_token(scope: dict) -> tuple[Optional[str], Optional[str]]:
        """Extract a browser-supplied access token from the query string."""

        raw_query = scope.get("query_string", b"")
        if not raw_query:
            return None, None

        if isinstance(raw_query, bytes):
            try:
                query = raw_query.decode("utf-8")
            except UnicodeDecodeError:
                return None, "missing or invalid auth"
        else:
            query = str(raw_query)

        params = parse_qs(query, keep_blank_values=True)
        tokens = params.get("access_token")
        if not tokens:
            return None, None
        if len(tokens) != 1:
            return None, "ambiguous auth"

        token = tokens[0]
        if not token:
            return None, "missing or invalid auth"

        return token, None

    def _prepare_authorization_headers(
        self, scope: dict, headers: Dict[str, str]
    ) -> tuple[Dict[str, str], Optional[str]]:
        """Overlay Authorization header based on browser token rules."""

        token, error = self._extract_browser_token(scope)
        if error:
            return dict(headers), error

        header_auth = headers.get("authorization")
        if token:
            if header_auth:
                return dict(headers), "ambiguous auth"
            updated = dict(headers)
            updated["authorization"] = f"Bearer {token}"
            return updated, None

        return dict(headers), None

    @staticmethod
    def _make_preview_from_bytes(payload: bytes, limit: int = 160) -> Optional[str]:
        if not payload:
            return None
        preview = payload.decode("utf-8", "replace")
        preview = preview.replace("\r", "\\r").replace("\n", "\\n")
        if len(preview) > limit:
            return f"{preview[: limit - 1]}…"
        return preview

    async def _update_backpressure(self, ctx: AdapterContext, queued: int) -> None:
        ctx.outbound_queue_depth = queued
        if ctx.backpressure_state == "off" and queued > QUEUE_ON_THRESHOLD:
            ctx.backpressure_state = "on"
            await self._publish(
                EVT_BACKPRESSURE_ON,
                ctx.sid,
                {"queue_depth": queued, "state": "on"},
            )
        elif ctx.backpressure_state == "on" and queued < QUEUE_OFF_THRESHOLD:
            ctx.backpressure_state = "off"
            await self._publish(
                EVT_BACKPRESSURE_OFF,
                ctx.sid,
                {"queue_depth": queued, "state": "off"},
            )


__all__ = [
    "ChatV2Adapter",
    "CHAT_V2_SUBPROTOCOL",
    "QUEUE_ON_THRESHOLD",
    "QUEUE_OFF_THRESHOLD",
    "EVT_BACKPRESSURE_ON",
    "EVT_BACKPRESSURE_OFF",
    "EVT_WS_OUTBOX_DROP",
]
