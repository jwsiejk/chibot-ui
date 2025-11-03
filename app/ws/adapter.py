"""chat.v2 WebSocket adapter for AskChip."""
from __future__ import annotations

import os
import asyncio
import contextlib
import logging
import math
import threading
import time
import sys, platform, socket, os, inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Literal, Mapping, Optional, Protocol, runtime_checkable

import json
from urllib.parse import parse_qs

from app import config
from app.logging_setup import current_sid
from app.security.jwt_utils import verify_ws_token
from app.telemetry import bus
from app.telemetry.exporter import FileExporter
from app.services.streaming_asr.speechmatics_client import (
    OfflineSpeechmaticsClient,
    SpeechmaticsClient,
)
from app.voice_v2 import (
    EVT_ASR_FINAL,
    EVT_ASR_OPEN,
    EVT_ASR_PARTIAL,
    EVT_ASR_READY,
    EVT_CHAT_USER,
    EVT_CLIENT_BANNER,
    EVT_CLIENT_AUTOSTART,
    EVT_CLIENT_MIC_OPEN,
    EVT_HUD_STATE,
    EVT_SESSION_STEP,
    EVT_TTS_END,
    EVT_TTS_MASK,
    EVT_CLIENT_LOG,
    EVT_WS_AUDIO_RECV,
    EVT_WS_AUDIO_SEND,
    EVT_WS_JSON_RECV,
    EVT_WS_JSON_SEND,
)
from app.ws.validator import validate_audio_header_against_policy, validate_frame

try:  # pragma: no cover - uvicorn is an optional dependency in tests
    from uvicorn.protocols.utils import ClientDisconnected
except Exception:  # pragma: no cover - fallback when uvicorn missing
    class ClientDisconnected(Exception):  # type: ignore[no-redef]
        """Fallback placeholder when uvicorn is unavailable."""


CHAT_V2_SUBPROTOCOL = "chat.v2"
TEXT_FRAME_LIMIT_BYTES = 64 * 1024
BINARY_FRAME_LIMIT_BYTES = 2 * 1024 * 1024
ALLOW_AUDIO_WITHOUT_ASR = os.getenv("ALLOW_AUDIO_WITHOUT_ASR", "0") == "1"
PING_MIN_INTERVAL_MS = 500
RATE_LIMIT_CAPACITY = 25
RATE_LIMIT_WINDOW_SECONDS = 2.0
RATE_LIMIT_CLOSE_CODE = 1013
_AUDIO_VIOLATION_LIMIT = 3

AUDIO_SEQ_WINDOW = 8

QUEUE_ON_THRESHOLD = 12
QUEUE_OFF_THRESHOLD = 6

_DEFAULT_WS_PING_INTERVAL_MS = 20_000
_HEARTBEAT_TIMEOUT_MS = 30_000

EVT_BACKPRESSURE_ON = "EVT_BACKPRESSURE_ON"
EVT_BACKPRESSURE_OFF = "EVT_BACKPRESSURE_OFF"

EVT_RATE_LIMIT = "EVT_RATE_LIMIT"

EVT_WS_OUTBOX_DROP = "EVT_WS_OUTBOX_DROP"

EVT_WS_JSON_SEND_SUMMARY = "ws.json.send_summary"
EVT_WS_JSON_RECV_SUMMARY = "ws.json.recv_summary"
EVT_WS_AUDIO_FIRST_CHUNK = "ws.audio.first_chunk"

_DIAG_NO_AUDIO_CHECK_DELAY_SECONDS = 8.5
_MIC_OPEN_TIMEOUT_SECONDS = 2.5

_OUTBOUND_ALLOWED_TYPES = {
    "policy.interaction",
    "info",
    "tts.start",
    "tts.end",
    "asr.ready",
    "asr.input.start",
    "asr.input.stop",
    "asr.partial",
    "asr.final",
    "asr.unavailable",
    "error",
    "chat.message",
    "chat.history",
    "dialog.plan",
    "hud.nudge",
}

_RATE_LIMIT_EXEMPT_TYPES = {
    "client.autostart",
    "client.banner",
    "client.diag",
    "client.log",
    "client.ping",
    "client.telemetry",
}

_OUTBOX_MAXSIZE = 256

_POLICY_STABLE_KEYS = (
    "mode",
    "allow_auto_vad",
    "barge_in_enabled",
    "auto_commit_when_ready",
    "voice",
    "greet",
    "suggestions",
    "actions",
    "telemetry",
    "media",
    "capture",
    "audio",
    "policy",
)

_log = logging.getLogger(__name__)

_ALLOWED_TEXT_FRAME_TYPES = {
    "client.ready",
    "audio.header",
    "admin.toggle",
    "chat.user",
    "client.diag",
    "client.log",
    "client.autostart",
    "client.telemetry",
    "client.banner",
    "asr.rearm.request",
}

_CLIENT_TELEMETRY_ALLOWED_EVENTS = {
    "EVT_RECORDER_STARTED",
    "EVT_AUDIO_CHUNK_SENT_CLIENT",
}

_CLIENT_AUTOSTART_ALLOWED_EVENTS = {
    "attempt",
    "armed",
    "blocked",
    "cta_click",
    "cta_shown",
    "error",
    "gesture",
    "max_attempts",
    "recording_started",
    "recording_stopped",
    "rejected",
}


class TokenBucket:
    """Simple in-memory token bucket."""

    def __init__(self, capacity: int, refill_seconds: float) -> None:
        self.capacity = capacity
        self.tokens = float(capacity)
        self.refill_seconds = refill_seconds
        self.last_refill = time.monotonic()

    def _peek_tokens(self, now: float) -> float:
        tokens = self.tokens
        elapsed = max(0.0, now - self.last_refill)
        if elapsed > 0 and self.refill_seconds > 0:
            refill = (self.capacity / self.refill_seconds) * elapsed
            if refill > 0:
                tokens = min(self.capacity, tokens + refill)
        return tokens

    def consume(self, count: int, now: Optional[float] = None) -> bool:
        if now is None:
            now = time.monotonic()
        available = self._peek_tokens(now)
        if available < count:
            return False
        if now > self.last_refill:
            self.tokens = available
            self.last_refill = now
        self.tokens -= count
        return True

    def retry_after(self, count: int = 1, now: Optional[float] = None) -> float:
        """Return seconds until ``count`` tokens are available."""

        if count <= 0:
            return 0.0
        if now is None:
            now = time.monotonic()
        available = self._peek_tokens(now)
        if available >= count:
            return 0.0
        if self.capacity <= 0 or self.refill_seconds <= 0:
            return math.inf
        deficit = count - available
        refill_rate = self.capacity / self.refill_seconds
        if refill_rate <= 0:
            return math.inf
        return deficit / refill_rate


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
    user_id: Optional[str] = None
    is_admin: bool = False
    principal: Dict[str, Any] = field(default_factory=dict)
    audio_seq: int = 0
    audio_expected_seq: int = 0
    audio_highest_seq: int = -1
    audio_buffer: Dict[int, bytes] = field(default_factory=dict)
    audio_window: int = AUDIO_SEQ_WINDOW
    audio_chunks_recv: int = 0
    audio_bytes_recv: int = 0
    ingress_packets: int = 0
    ingress_bytes: int = 0
    first_ingress_ms: Optional[int] = None
    mic_armed_ms: Optional[int] = None
    asr_ready_bundle_sent_ms: Optional[int] = None
    last_pong_sent_ms: int = 0
    last_client_activity_ms: int = 0
    last_client_pong_ms: int = 0
    last_server_ping_ms: int = 0
    ip: Optional[str] = None
    sid_bucket: Optional[TokenBucket] = None
    ip_bucket: Optional[TokenBucket] = None
    audio_profile: Optional[Dict[str, Any]] = None
    accepting_audio: bool = True
    audio_violation_count: int = 0
    awaiting_asr_ready: bool = False
    client_capture_armed: bool = False
    outbound_queue_depth: int = 0
    backpressure_state: Literal["off", "on"] = "off"
    outbox: asyncio.Queue[Dict[str, Any]] | None = None
    outbound_task: asyncio.Task[None] | None = None
    subscription_token: Optional[str] = None
    audio_subscription_token: Optional[str] = None
    audio_tasks: set[asyncio.Task[None]] = field(default_factory=set)
    audio_send_closed: bool = False
    asr_ready: bool = False
    asr_subscription_token: Optional[str] = None
    asr_subscription_bus: Optional[Any] = None
    asr_open_subscription_token: Optional[str] = None
    asr_unavailable_subscription_token: Optional[str] = None
    asr_partial_subscription_token: Optional[str] = None
    asr_final_subscription_token: Optional[str] = None
    asr_recovering_until: float = 0.0
    asr_recovering_reason: Optional[str] = None
    asr_recovering_audio_logged: bool = False
    server_keepalive_task: asyncio.Task[None] | None = None
    last_policy_interaction: Optional[Dict[str, Any]] = None
    policy_snapshot: Optional[Dict[str, Any]] = None
    partial_seq: int = 0
    partial_coalescer: _PartialCoalescer = field(default_factory=_PartialCoalescer)
    send_lock: asyncio.Lock | None = None
    tts_end_ts: Optional[float] = None
    diag_audio_seen: bool = False
    diag_timer: asyncio.TimerHandle | None = None
    diag_timer_key: Optional[str] = None
    await_user_expected: bool = False
    await_user_pending: bool = False
    await_user_pending_key: Optional[str] = None
    await_user_after_mask: bool = False
    await_user_after_mask_key: Optional[str] = None
    tts_mask_phase: str = "off"
    mask_subscription_token: Optional[str] = None
    tts_end_subscription_token: Optional[str] = None
    turn_state_subscription_token: Optional[str] = None
    hud_state: Optional[str] = None
    client_mic_open: bool = False
    mic_open_timer: asyncio.TimerHandle | None = None
    mic_nudge_sent: bool = False
    await_user_req_id: Optional[str] = None
    last_tts_end_req_id: Optional[str] = None
    await_user_cue_emitted: bool = False
    listen_handoff_done: set[str] = field(default_factory=set)
    listen_handoff_task: asyncio.Task[None] | None = None
    listen_handoff_task_key: Optional[str] = None
    listen_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    client_banner_info: Optional[Dict[str, Any]] = None
    client_banner_events: List[Dict[str, Any]] = field(default_factory=list)
    allowed_asr_vendors: List[str] = field(default_factory=list)
    asr_vendor: Optional[str] = None
    audio_pipeline_mode: Optional[str] = None
    asr_vendor_logged: bool = False
    session_capture_policy: Optional[Dict[str, Any]] = None


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
        self._ping_interval_ms = max(
            0,
            int(os.getenv("WS_PING_INTERVAL_MS", str(_DEFAULT_WS_PING_INTERVAL_MS))),
        )
        self.tts_runtime = None
        self.asr_runtime = None
        self._asr_runtime_vendor: Optional[str] = None

    @staticmethod
    def _turn_key(ctx: AdapterContext, req_id: Optional[str]) -> Optional[str]:
        if isinstance(req_id, str) and req_id:
            return f"{ctx.sid}:{req_id}"
        return None

    @staticmethod
    def _set_pending_for_key(ctx: AdapterContext, key: Optional[str]) -> None:
        ctx.await_user_pending = True
        ctx.await_user_pending_key = key

    @staticmethod
    def _clear_pending_for_key(ctx: AdapterContext, key: Optional[str]) -> None:
        if key is None:
            if ctx.await_user_pending_key is None:
                ctx.await_user_pending = False
            return
        if ctx.await_user_pending_key == key:
            ctx.await_user_pending = False
            ctx.await_user_pending_key = None

    @staticmethod
    def _set_after_mask_for_key(ctx: AdapterContext, key: Optional[str]) -> None:
        ctx.await_user_after_mask = True
        ctx.await_user_after_mask_key = key

    @staticmethod
    def _clear_after_mask_for_key(ctx: AdapterContext, key: Optional[str]) -> None:
        if key is None:
            if ctx.await_user_after_mask_key is None:
                ctx.await_user_after_mask = False
            return
        if ctx.await_user_after_mask_key == key:
            ctx.await_user_after_mask = False
            ctx.await_user_after_mask_key = None

    def _maybe_emit_await_user(self, ctx: Any, policy: Dict[str, Any]) -> None:
        capture = policy.get("capture") if isinstance(policy, dict) else None
        start_on_ready = bool(capture.get("start_on_turn_ready")) if isinstance(capture, dict) else False
        sid = getattr(ctx, "sid", "")
        if not getattr(ctx, "await_user_expected", False):
            return
        if getattr(ctx, "await_user_cue_emitted", False):
            return
        outbox = getattr(ctx, "outbox", None)
        if outbox is None:
            return
        payload: Dict[str, Any] = {
            "type": "assistant.await_user",
            "reason": "tts_end",
            "ts": self._now_ms(),
        }
        req_value = getattr(ctx, "await_user_req_id", None)
        if isinstance(req_value, str) and req_value:
            payload["req_id"] = req_value
        try:
            outbox.put_nowait(payload)
        except asyncio.QueueFull:
            now = outbox.qsize()
            try:
                asyncio.create_task(
                    self._publish(
                        EVT_WS_OUTBOX_DROP,
                        sid,
                        {"sid": sid, "dropped": 1, "now": now},
                    )
                )
            except RuntimeError:
                pass
            return
        ctx.await_user_cue_emitted = True
        _log.info(
            "evt=turn_listen_cue sid=%s reason=tts_end policy_start=%s",
            sid,
            "true" if start_on_ready else "false",
        )
        if isinstance(req_value, str) and req_value:
            _log.info("evt=await_user_emit sid=%s req_id=%s", sid, req_value)
        else:
            _log.info("evt=await_user_emit sid=%s", sid)

    async def __call__(self, scope: dict, receive: Callable[[], Awaitable[dict]], send: Callable[[dict], Awaitable[None]]) -> None:
        if scope.get("type") != "websocket":
            raise RuntimeError("ChatV2Adapter can only handle websocket scopes")

        connect_event = await receive()
        if connect_event.get("type") != "websocket.connect":
            return

        raw_path = scope.get("raw_path")
        if isinstance(raw_path, bytes):
            try:
                path = raw_path.decode("utf-8")
            except UnicodeDecodeError:  # pragma: no cover - defensive fallback
                path = raw_path.decode("latin-1", errors="ignore")
        else:
            path = scope.get("path", "")

        query_bytes = scope.get("query_string") or b""
        try:
            query_string = query_bytes.decode("utf-8")
        except UnicodeDecodeError:  # pragma: no cover - defensive fallback
            query_string = query_bytes.decode("latin-1", errors="ignore")
        path_qs = path
        if query_string:
            path_qs = f"{path}?{query_string}"

        subprotocols = scope.get("subprotocols") or []
        _log.info("evt=ws_subs subprotocols=%r need='chat.v2'", subprotocols)
        if CHAT_V2_SUBPROTOCOL not in subprotocols:
            _log.warning(
                "evt=ws_accept_reject code=4401 reason=bad_subprotocol path=%s", path_qs
            )
            await self._reject_subprotocol(send)
            return

        headers = self._decode_headers(scope.get("headers", ()))

        query_params = parse_qs(query_string, keep_blank_values=True)
        query_token = query_params.get("access_token", [None])[0]
        token = query_token

        if not token:
            auth_header = headers.get("authorization")
            if isinstance(auth_header, str) and auth_header:
                parts = auth_header.split(" ", 1)
                if parts and parts[0].lower() == "bearer" and len(parts) == 2:
                    candidate = parts[1].strip()
                    if candidate:
                        token = candidate

        if not token:
            for p in subprotocols or []:
                if isinstance(p, str) and p.startswith("jwt.") and len(p) > 4:
                    token = p[4:]
                    break

        if not token:
            reason = "missing_token"
            _log.warning(
                "evt=ws_accept_reject code=4401 reason=%s path=%s", reason, path_qs
            )
            await send({"type": "websocket.close", "code": 4401, "reason": reason})
            return

        try:
            claims = verify_ws_token(token)
        except Exception:
            reason = "jwt_invalid_or_expired"
            _log.warning(
                "evt=ws_accept_reject code=4401 reason=%s path=%s", reason, path_qs
            )
            await send({"type": "websocket.close", "code": 4401, "reason": reason})
            return

        sid = claims.get("sid")
        sub = claims.get("sub")
        aud = claims.get("aud")
        if not sid or not sub or aud != CHAT_V2_SUBPROTOCOL:
            reason = "jwt_claims_invalid"
            _log.warning(
                "evt=ws_accept_reject code=4401 reason=%s path=%s", reason, path_qs
            )
            await send({"type": "websocket.close", "code": 4401, "reason": reason})
            return

        principal: Dict[str, Any] = dict(claims)
        is_admin = bool(principal.get("is_admin"))

        self._emit_session_step(
            sid,
            "ws.token.validated",
            summary="Validated WebSocket token",
            meta={"sub": sub, "is_admin": is_admin},
            source="ws.accept",
        )

        _log.info("evt=ws_accept_token_ok sid=%s", sid)

        _log.info("evt=ws_accept subprotocol='%s'", CHAT_V2_SUBPROTOCOL)
        await send({"type": "websocket.accept", "subprotocol": CHAT_V2_SUBPROTOCOL})

        self._emit_session_step(
            sid,
            "ws.accepted",
            summary="Accepted WebSocket upgrade",
            meta={"subprotocol": CHAT_V2_SUBPROTOCOL},
            source="ws.accept",
        )

        # ---- BEGIN RUNTIME BANNER ----
        try:
            adapter_file = __file__
            engine_file = None
            asr_file = None
            if self.engine is not None:
                eng_mod = sys.modules.get(self.engine.__class__.__module__)
                engine_file = getattr(eng_mod, "__file__", None)
            if getattr(self, "asr_runtime", None) is not None:
                asr_mod = sys.modules.get(self.asr_runtime.__class__.__module__)
                asr_file = getattr(asr_mod, "__file__", None)

            build_id = os.getenv("BUILD_ID", "") or os.getenv("SOURCE_VERSION", "") or "unknown"
            host = socket.gethostname()
            pid = os.getpid()
            cwd = os.getcwd()

            offered = scope.get("subprotocols", [])
            selected = "chat.v2"

            banner = {
                "type": "server.banner",
                "build_id": build_id,
                "host": host,
                "pid": pid,
                "cwd": cwd,
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "ws_path": scope.get("path"),
                "subprotocols_offered": offered,
                "subprotocol_selected": selected,
                "adapter_file": adapter_file,
                "engine_file": engine_file,
                "asr_file": asr_file,
            }

            await self._send_json(send, sid, banner)
            _log.info(
                "evt=server_banner build_id=%s host=%s pid=%d path=%s subproto=%s adapter=%s engine=%s asr=%s",
                build_id,
                host,
                pid,
                scope.get("path"),
                selected,
                adapter_file,
                engine_file,
                asr_file,
            )
            self._emit_session_step(
                sid,
                "ws.banner_sent",
                summary="Sent server banner",
                meta={
                    "build_id": build_id,
                    "host": host,
                    "pid": pid,
                    "subprotocol": selected,
                },
            )
        except Exception:
            _log.exception("evt=server_banner_emit_failed")
        # ---- END RUNTIME BANNER ----

        now_ms = int(time.time() * 1000)
        info_frame: Dict[str, Any] = {
            "type": "info",
            "protocol": CHAT_V2_SUBPROTOCOL,
            "sid": sid,
            "ts_ms": now_ms,
            "build_id": os.getenv("BUILD_ID") or os.getenv("RENDER_GIT_COMMIT") or "",
        }
        info_frame["meta"] = {"sid": sid}
        policy_snapshot = self._policy_snapshot()
        allowed_asr_vendors = self._allowed_asr_vendors()
        snapshot_mapping: Mapping[str, Any] | None
        if isinstance(policy_snapshot, Mapping):
            snapshot_mapping = policy_snapshot
        else:
            snapshot_mapping = None

        provisional_ctx = AdapterContext(
            sid=sid,
            headers=dict(headers),
            principal=principal,
            user_id=sub,
            is_admin=is_admin,
        )
        provisional_ctx.allowed_asr_vendors = list(allowed_asr_vendors)
        selected_vendor, selection_reason = self._select_asr_vendor(
            provisional_ctx,
            snapshot_mapping,
            allowed_asr_vendors,
            log_selection=False,
        )
        if policy_snapshot:
            self._log_policy_flags(sid, policy_snapshot)
            info_frame["policy"] = policy_snapshot
        await self._send_json(send, sid, info_frame)
        _log.info("evt=ws_info_sent sid=%s", sid)
        self._emit_session_step(
            sid,
            "ws.info_sent",
            summary="Sent info frame",
            meta={"has_policy": bool(policy_snapshot)},
        )

        ctx = AdapterContext(
            sid=sid,
            headers=dict(headers),
            principal=principal,
            user_id=sub,
            is_admin=is_admin,
        )
        ctx.policy_snapshot = dict(policy_snapshot) if isinstance(policy_snapshot, dict) else policy_snapshot
        ctx.last_client_activity_ms = now_ms
        ctx.allowed_asr_vendors = list(allowed_asr_vendors)
        snapshot_mapping = (
            ctx.policy_snapshot if isinstance(ctx.policy_snapshot, Mapping) else None
        )
        ctx.asr_vendor = selected_vendor or "speechmatics"
        allowed_display = ",".join(ctx.allowed_asr_vendors)
        _log.info(
            "asr_vendor_selected primary=%s allowed=%s reason=%s",
            ctx.asr_vendor,
            allowed_display,
            selection_reason,
        )
        ctx.asr_vendor_logged = True
        ctx.audio_pipeline_mode = self._resolve_audio_pipeline_mode(snapshot_mapping)
        if ctx.asr_vendor == "speechmatics":
            ctx.audio_pipeline_mode = "pcm16"
        mode = ctx.audio_pipeline_mode or "pcm16"
        ctx.session_capture_policy = self._session_capture_policy_for_mode(mode)

        token = current_sid.set(ctx.sid)
        try:
            if not (
                inspect.iscoroutinefunction(self._on_open_and_greet)
                and inspect.iscoroutinefunction(self._invoke_engine)
            ):
                _log.error("evt=ws_async_contract_violation sid=%s", ctx.sid)
                raise RuntimeError("WebSocket async contract violation")

            ctx.sid_bucket = TokenBucket(RATE_LIMIT_CAPACITY, RATE_LIMIT_WINDOW_SECONDS)
            client = scope.get("client")
            ctx.ip = client[0] if isinstance(client, tuple) and client else None
            if ctx.ip:
                ctx.ip_bucket = self._ip_buckets.setdefault(
                    ctx.ip,
                    TokenBucket(RATE_LIMIT_CAPACITY, RATE_LIMIT_WINDOW_SECONDS),
                )

            had_active_contexts = bool(self._contexts)
            self._contexts[ctx.sid] = ctx
            current_runtime = getattr(self, "asr_runtime", None)
            current_vendor = getattr(self, "_asr_runtime_vendor", None)
            if (
                current_runtime is not None
                and isinstance(ctx.asr_vendor, str)
                and ctx.asr_vendor
                and current_vendor
                and ctx.asr_vendor != current_vendor
                and not had_active_contexts
            ):
                self.asr_runtime = None
                current_runtime = None
                self._asr_runtime_vendor = None

            if getattr(self, "asr_runtime", None) is None:
                try:
                    from app.voice_v2.asr_runtime import ASRRuntime

                    engine = getattr(self, "engine", None)
                    vendor = ctx.asr_vendor or "speechmatics"
                    client = None
                    if engine is not None and vendor:
                        if vendor == "speechmatics":
                            sm_enabled = getattr(config, "ASR_SPEECHMATICS_ENABLED", False)
                            sm_key = getattr(config, "SPEECHMATICS_API_KEY", "")
                            sm_url = getattr(config, "SPEECHMATICS_REALTIME_URL", "")
                            if sm_enabled and sm_key and sm_url:
                                client = SpeechmaticsClient(sm_key, sm_url, bus, _log)
                            elif sm_enabled:
                                client = OfflineSpeechmaticsClient(bus, _log)
                            else:
                                _log.warning(
                                    "evt=ws_asr_runtime_unavailable sid=%s vendor=%s has_engine=%s has_api_key=%s has_url=%s",
                                    ctx.sid,
                                    vendor,
                                    bool(engine),
                                    bool(sm_key),
                                    bool(sm_url),
                                )
                        else:
                            _log.warning(
                                "evt=ws_asr_runtime_unsupported_vendor sid=%s vendor=%s",
                                ctx.sid,
                                vendor,
                            )

                    if engine is not None and client is not None:
                        self.asr_runtime = ASRRuntime(
                            engine=engine,
                            client=client,
                            telemetry_bus=bus,
                        )
                        self._asr_runtime_vendor = vendor or None
                    elif engine is None:
                        _log.warning(
                            "evt=ws_asr_runtime_unavailable sid=%s has_engine=%s vendor=%s",
                            ctx.sid,
                            bool(engine),
                            vendor or "",
                        )
                except Exception:  # pragma: no cover - defensive logging
                    _log.exception("evt=ws_asr_runtime_attach_failed sid=%s", ctx.sid)

            asr_runtime = getattr(self, "asr_runtime", None)
            if asr_runtime is not None and hasattr(asr_runtime, "set_bus"):
                try:
                    asr_runtime.set_bus(bus)
                except Exception:  # pragma: no cover - defensive logging
                    _log.exception("evt=ws_asr_set_bus_failed sid=%s", ctx.sid)

            _log.info("evt=ws_open sid=%s", ctx.sid)
            runtime_name = type(asr_runtime).__name__ if asr_runtime is not None else "None"
            _log.info("evt=ws_open sid=%s asr_runtime=%s", ctx.sid, runtime_name)
            if asr_runtime is not None:
                try:
                    asr_runtime.on_ws_open(ctx.sid)
                except Exception:  # pragma: no cover - defensive logging
                    _log.exception("evt=ws_asr_open_failed sid=%s", ctx.sid)
            self._start_asr_ready_tracker(ctx, bus)
            self._start_outbound_bridge(ctx, send)
            self._start_server_keepalive(ctx, send)

            if self.exporter:
                self.exporter.begin(ctx.sid)
                self._emit_session_step(
                    ctx.sid,
                    "session.export_started",
                    summary="Started session export",
                    source="ws.exporter",
                )

            await self._on_open_and_greet(ctx)

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
                asr_runtime = getattr(self, "asr_runtime", None)
                if asr_runtime is not None:
                    try:
                        asr_runtime.on_ws_close(ctx.sid)
                    except Exception:  # pragma: no cover - defensive logging
                        _log.exception("evt=ws_asr_close_failed sid=%s", ctx.sid)
                await self._invoke_engine("on_close", ctx.sid, close_code, close_reason)
                if self.exporter:
                    self.exporter.end(ctx.sid, {"close_code": close_code})
                    self._emit_session_step(
                        ctx.sid,
                        "session.export_completed",
                        summary="Completed session export",
                        meta={"close_code": close_code, "close_reason": close_reason},
                        source="ws.exporter",
                    )
                self._contexts.pop(ctx.sid, None)
                self._emit_session_step(
                    ctx.sid,
                    "ws.closed",
                    summary="WebSocket session closed",
                    meta={"close_code": close_code, "close_reason": close_reason},
                    source="ws.close",
                )
        finally:
            current_sid.reset(token)

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
        detail = "use chat.v2"
        body = json.dumps(
            {
                "type": "error",
                "code": "bad_subprotocol",
                "detail": detail,
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

        frame_payload: Optional[Dict[str, Any]] = None

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

        frame_payload = frame

        raw_type = frame.get("type")
        if not isinstance(raw_type, str):
            meta["error"] = "schema_invalid"
            await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta, frame_payload)
            await self._send_error(send, ctx.sid, "schema_invalid", "Frame missing type field")
            return self._HandleResult(True)

        frame_type = raw_type
        meta["frame_type"] = frame_type
        now_ms = int(time.time() * 1000)
        ctx.last_client_activity_ms = now_ms

        if frame_type not in _RATE_LIMIT_EXEMPT_TYPES:
            limited = await self._check_rate_limit(ctx, send)
            if limited is not None:
                return limited

        if frame_type == "client.ping":
            await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta, frame_payload)
            if now_ms - ctx.last_pong_sent_ms >= PING_MIN_INTERVAL_MS:
                ctx.last_pong_sent_ms = now_ms
                client_ts = frame.get("ts")
                payload = {"type": "server.pong", "ts": now_ms}
                if isinstance(client_ts, int):
                    payload["echo"] = client_ts
                await self._send_json(send, ctx.sid, payload)
            return self._HandleResult(True)

        if frame_type == "client.pong":
            await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta, frame_payload)
            ctx.last_client_pong_ms = now_ms
            return self._HandleResult(True)

        if frame_type == "ping":
            await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta, frame_payload)
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
                await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta, frame_payload)
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
                await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta, frame_payload)
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
                await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta, frame_payload)
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
                await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta, frame_payload)
                await self._send_error(send, ctx.sid, "unknown_type", frame_type)
                return self._HandleResult(True)

            is_valid, hint = validate_frame(frame)
            if not is_valid:
                meta["error"] = "schema_invalid"
                await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta, frame_payload)
                detail = hint or "Frame failed validation"
                await self._send_error(send, ctx.sid, "schema_invalid", detail)
                return self._HandleResult(True)

        if frame_type == "audio.header":
            profile = {
                "format": "pcm",
                "codec": "pcm_s16le",
                "sample_rate": frame.get("sample_rate"),
                "channels": frame.get("channels"),
                "container": "raw",
            }
            seq_start = frame.get("seq_start")
            if seq_start is not None:
                if not isinstance(seq_start, int):
                    meta["error"] = "schema_invalid"
                    await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta, frame_payload)
                    await self._send_error(
                        send,
                        ctx.sid,
                        "schema_invalid",
                        "audio.header seq_start must be an integer",
                    )
                    return self._HandleResult(False, 1003, "schema_invalid")
                if seq_start < 0:
                    meta["error"] = "schema_invalid"
                    await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta, frame_payload)
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
                await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta, frame_payload)
                await self._send_error(
                    send,
                    ctx.sid,
                    "schema_invalid",
                    "duplicate or conflicting audio.header",
                )
                return self._HandleResult(True)
            err = validate_audio_header_against_policy(
                frame, ctx.policy_snapshot, ctx.asr_vendor
            )
            if err:
                meta["error"] = "policy_violation"
                await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta, frame_payload)
                _log.warning("evt=policy_violation sid=%s err=%s", ctx.sid, err)
                await self._send_json(send, ctx.sid, {"type": "error", "error": err})
                return self._HandleResult(False, 4400, "policy_violation")
            ctx.audio_profile = profile
            if seq_start is not None:
                ctx.audio_seq = max(0, seq_start)
                ctx.audio_expected_seq = ctx.audio_seq
                ctx.audio_highest_seq = ctx.audio_seq - 1
                ctx.audio_buffer.clear()
            audio_meta = {
                key: value
                for key, value in (
                    ("format", profile.get("format")),
                    ("sample_rate", profile.get("sample_rate")),
                    ("channels", profile.get("channels")),
                    ("seq_start", profile.get("seq_start")),
                )
                if value is not None
            }
            self._emit_session_step(
                ctx.sid,
                "audio.header.accepted",
                summary="Accepted audio header",
                meta=audio_meta,
                source="ws.audio",
            )

        if frame_type == "asr.rearm.request":
            keep_warm_ms = self._policy_keep_warm_ms(ctx)
            runtime = getattr(self, "asr_runtime", None)
            if runtime is not None:
                prearm = getattr(runtime, "prearm", None)
                if callable(prearm):
                    try:
                        prearm(ctx.sid, keep_warm_ms=keep_warm_ms)
                    except TypeError:
                        prearm(ctx.sid)
                    except Exception:  # pragma: no cover - defensive logging
                        _log.exception("evt=ws_asr_prearm_failed sid=%s", ctx.sid)
            _log.info(
                "evt=asr_prearm_requested sid=%s source=client keep_stream_warm_ms=%s",
                ctx.sid,
                keep_warm_ms,
            )
            try:
                await self._send_asr_ready_bundle(send, ctx)
            except Exception:  # pragma: no cover - defensive logging
                _log.exception("evt=ws_asr_ready_bundle_failed sid=%s", ctx.sid)

        if frame_type == "client.ready":
            mic_info = frame.get("mic")
            if isinstance(mic_info, dict):
                state = mic_info.get("state")
                if isinstance(state, str) and state.lower() == "open":
                    vendor = mic_info.get("vendor")
                    opened_ts = mic_info.get("ts")
                    ts_value = opened_ts if isinstance(opened_ts, int) else None
                    self._emit_client_mic_open(
                        ctx,
                        vendor=vendor if isinstance(vendor, str) else None,
                        opened_ts=ts_value,
                    )
                    ctx.mic_nudge_sent = False
                ready_meta = {
                    key: value
                    for key, value in (
                        ("state", state if isinstance(state, str) else None),
                        (
                            "vendor",
                            mic_info.get("vendor")
                            if isinstance(mic_info.get("vendor"), str)
                            else None,
                        ),
                    )
                    if value is not None
                }
            else:
                ready_meta = {}
            self._emit_session_step(
                ctx.sid,
                "client.ready",
                summary="Client reported ready",
                meta=ready_meta,
                source="ws.client",
            )

        if frame_type == "client.log":
            sanitized_log = self._sanitize_client_log(frame)
            if sanitized_log:
                meta["client_log"] = {
                    key: value
                    for key, value in sanitized_log.items()
                    if key != "detail"
                }
                detail_payload = sanitized_log.get("detail")
                stage_value: Optional[str] = None
                vendor_value: Optional[str] = None
                outcome = None
                attempts = None
                if isinstance(detail_payload, Mapping):
                    outcome = detail_payload.get("outcome")
                    attempts = detail_payload.get("attempts")
                    stage_candidate = detail_payload.get("stage")
                    if isinstance(stage_candidate, str) and stage_candidate.strip():
                        stage_value = stage_candidate.strip().lower()
                    vendor_candidate = detail_payload.get("vendor")
                    if isinstance(vendor_candidate, str) and vendor_candidate.strip():
                        vendor_value = vendor_candidate.strip().lower()
                _log.info(
                    "evt=ws_client_log sid=%s label=%s outcome=%s attempts=%s",
                    ctx.sid,
                    sanitized_log.get("label"),
                    outcome,
                    attempts,
                )
                if vendor_value == "speechmatics" and stage_value in {"ready", "started", "closed"}:
                    bus.publish(
                        {
                            "type": EVT_WS_JSON_RECV_SUMMARY,
                            "sid": ctx.sid,
                            "who": "server",
                            "source": "ws.adapter",
                            "meta": {
                                "kind": stage_value,
                                "vendor": "speechmatics",
                            },
                        }
                    )
                bus.publish(
                    {
                        "type": EVT_CLIENT_LOG,
                        "sid": ctx.sid,
                        "who": "client",
                        "source": "client_log",
                        "meta": sanitized_log,
                    }
                )
            else:
                _log.info(
                    "evt=ws_client_log sid=%s label=%s detail=empty",
                    ctx.sid,
                    frame.get("label"),
                )

        if frame_type == "client.banner":
            sanitized_info = self._sanitize_client_banner_info(frame.get("info"))
            if sanitized_info:
                ctx.client_banner_info = sanitized_info
            elif ctx.client_banner_info is None:
                ctx.client_banner_info = {}
            sanitized_event = self._sanitize_client_banner_event(frame.get("event"))
            if sanitized_event is None:
                meta["error"] = "schema_invalid"
                await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta, frame_payload)
                await self._send_error(
                    send,
                    ctx.sid,
                    "schema_invalid",
                    "client.banner event requires a label",
                )
                return self._HandleResult(True)
            ctx.client_banner_events.append(dict(sanitized_event))
            if len(ctx.client_banner_events) > 64:
                del ctx.client_banner_events[: len(ctx.client_banner_events) - 64]
            log_meta: Dict[str, Any] = {
                "label": sanitized_event.get("label"),
                "client_ts_ms": sanitized_event.get("ts_ms"),
            }
            event_meta = sanitized_event.get("meta")
            if event_meta:
                log_meta["event_meta"] = event_meta
            if ctx.client_banner_info:
                log_meta["info"] = ctx.client_banner_info
            meta["client_banner"] = {"label": sanitized_event.get("label")}
            bus.publish(
                {
                    "type": EVT_CLIENT_BANNER,
                    "sid": ctx.sid,
                    "who": "client",
                    "source": "ws_client",
                    "meta": log_meta,
                }
            )

        if frame_type == "client.autostart":
            event_name = frame.get("event")
            if not isinstance(event_name, str) or not event_name.strip():
                meta["error"] = "schema_invalid"
                await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta, frame_payload)
                await self._send_error(
                    send,
                    ctx.sid,
                    "schema_invalid",
                    "client.autostart requires an event label",
                )
                return self._HandleResult(True)

            normalized_event = event_name.strip()
            if normalized_event not in _CLIENT_AUTOSTART_ALLOWED_EVENTS:
                meta["error"] = "schema_invalid"
                await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta, frame_payload)
                await self._send_error(
                    send,
                    ctx.sid,
                    "schema_invalid",
                    "client.autostart event must be a supported value",
                )
                return self._HandleResult(True)

            sanitized_meta = self._sanitize_autostart_meta(frame.get("meta"))
            event_payload: Dict[str, Any] = {"event": normalized_event}
            if sanitized_meta:
                event_payload["meta"] = sanitized_meta
            meta["client_autostart"] = dict(event_payload)
            bus.publish(
                {
                    "schema_version": "1",
                    "type": EVT_CLIENT_AUTOSTART,
                    "sid": ctx.sid,
                    "who": "client",
                    "source": "ws_client",
                    "meta": event_payload,
                }
            )

        if frame_type == "client.telemetry":
            event_name = frame.get("event")
            if event_name not in _CLIENT_TELEMETRY_ALLOWED_EVENTS:
                meta["error"] = "schema_invalid"
                await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta, frame_payload)
                await self._send_error(
                    send,
                    ctx.sid,
                    "schema_invalid",
                    "client.telemetry event must be a supported value",
                )
                return self._HandleResult(True)

            frame_sid = frame.get("sid")
            if frame_sid is not None and not isinstance(frame_sid, str):
                meta["error"] = "schema_invalid"
                await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta, frame_payload)
                await self._send_error(
                    send,
                    ctx.sid,
                    "schema_invalid",
                    "client.telemetry sid must be a string if provided",
                )
                return self._HandleResult(True)

            telemetry_meta = frame.get("meta")
            if telemetry_meta is None:
                telemetry_meta_dict: Dict[str, Any] = {}
            elif isinstance(telemetry_meta, Mapping):
                telemetry_meta_dict = dict(telemetry_meta)
            else:
                meta["error"] = "schema_invalid"
                await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta, frame_payload)
                await self._send_error(
                    send,
                    ctx.sid,
                    "schema_invalid",
                    "client.telemetry meta must be an object if provided",
                )
                return self._HandleResult(True)

            bus.publish(
                {
                    "schema_version": "1",
                    "type": event_name,
                    "sid": frame_sid if isinstance(frame_sid, str) else ctx.sid,
                    "who": "client",
                    "source": "browser",
                    "meta": telemetry_meta_dict,
                }
            )

        await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta, frame_payload)
        await self._invoke_engine("on_json", ctx.sid, frame)
        return self._HandleResult(True)

    async def _handle_binary(
        self, data: bytes, ctx: AdapterContext, send: Callable[[dict], Awaitable[None]]
    ) -> _HandleResult:
        byte_count = len(data)
        ctx.last_client_activity_ms = int(time.time() * 1000)

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

        if not ctx.client_capture_armed:
            if ALLOW_AUDIO_WITHOUT_ASR:
                _log.warning("evt=asr_guard_bypassed sid=%s", ctx.sid)
            else:
                meta = {
                    "byte_count": byte_count,
                    "error": "audio_not_expected",
                    "ws": {"dir": "in", "size": byte_count},
                }
                await self._publish(EVT_WS_AUDIO_RECV, ctx.sid, meta)
                await self._send_error(send, ctx.sid, "audio_not_expected", "asr not ready")
                return self._HandleResult(False, 1003, "audio_not_expected")

        now = time.monotonic()
        awaiting_ready = ctx.awaiting_asr_ready and not ctx.asr_ready
        if not ctx.asr_ready:
            if awaiting_ready:
                pass
            else:
                grace_deadline = getattr(ctx, "asr_recovering_until", 0.0) or 0.0
                if grace_deadline and now <= grace_deadline:
                    if not ctx.asr_recovering_audio_logged:
                        remaining_ms = max(0, int((grace_deadline - now) * 1000.0))
                        reason_label = ctx.asr_recovering_reason or "unspecified"
                        _log.warning(
                            "evt=asr_guard_grace sid=%s remaining_ms=%s reason=%s",
                            ctx.sid,
                            remaining_ms,
                            reason_label,
                        )
                        ctx.asr_recovering_audio_logged = True
                else:
                    if grace_deadline:
                        ctx.asr_recovering_until = 0.0
                        ctx.asr_recovering_reason = None
                        ctx.asr_recovering_audio_logged = False
                    if ALLOW_AUDIO_WITHOUT_ASR:
                        _log.warning("evt=asr_guard_bypassed sid=%s", ctx.sid)
                    else:
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
        now_ms = int(time.time() * 1000)
        if ctx.ingress_packets <= 0:
            ctx.ingress_packets = 0
            ctx.ingress_bytes = 0
            ctx.first_ingress_ms = now_ms
        ctx.ingress_packets += 1
        pkt_len = byte_count
        ctx.ingress_bytes += pkt_len

        if ctx.ingress_packets == 1:
            armed_ms = ctx.mic_armed_ms if isinstance(ctx.mic_armed_ms, int) else None
            delta = (now_ms - armed_ms) if armed_ms is not None else None
            ready_sent_ms = (
                ctx.asr_ready_bundle_sent_ms
                if isinstance(ctx.asr_ready_bundle_sent_ms, int)
                else None
            )
            ready_delta = (now_ms - ready_sent_ms) if ready_sent_ms is not None else None
            buffer_bytes = 0
            if ctx.audio_buffer:
                buffer_bytes = sum(
                    len(chunk)
                    for chunk in ctx.audio_buffer.values()
                    if isinstance(chunk, (bytes, bytearray, memoryview))
                )
            ready_delta_value = ready_delta if ready_delta is not None else -1
            _log.info(
                "evt=ws_audio_ingress sid=%s first_chunk=1 bytes=%s first_chunk_ms_since_mic_armed=%s ms_since_asr_ready_bundle=%s buffer_bytes=%s",
                ctx.sid,
                pkt_len,
                delta,
                ready_delta_value,
                buffer_bytes,
            )
            delta_ms = ready_delta_value
            _log.info(
                "evt=ws_audio_first_chunk sid=%s bytes=%d ms_since_asr_ready_bundle=%d",
                ctx.sid,
                pkt_len,
                delta_ms,
            )
            bus.publish(
                {
                    "type": EVT_WS_AUDIO_FIRST_CHUNK,
                    "sid": ctx.sid,
                    "who": "server",
                    "source": "ws.adapter",
                    "meta": {
                        "bytes": pkt_len,
                        "ms_since_ready": delta_ms,
                    },
                }
            )
        elif ctx.ingress_packets % 50 == 0:
            _log.info(
                "evt=ws_audio_ingress sid=%s packets=%s bytes=%s",
                ctx.sid,
                ctx.ingress_packets,
                ctx.ingress_bytes,
            )

        if (
            ctx.asr_vendor == "speechmatics"
            and ctx.audio_chunks_recv == 0
            and data.startswith(b"\x1A\x45\xDF\xA3")
        ):
            meta = {
                "byte_count": byte_count,
                "error": "unexpected_container",
                "ws": {"dir": "in", "size": byte_count},
            }
            await self._publish(EVT_WS_AUDIO_RECV, ctx.sid, meta)
            detail = "speechmatics requires raw pcm audio"
            await self._send_error(send, ctx.sid, "audio_container_mismatch", detail)
            self._emit_session_step(
                ctx.sid,
                "audio.stream_closed",
                summary="Closed audio stream due to unexpected container",
                meta={"reason": "unexpected_container", "vendor": "speechmatics"},
                source="ws.audio",
            )
            _log.error(
                "asr_stream_closed reason=unexpected_container vendor=speechmatics sid=%s",
                ctx.sid,
            )
            return self._HandleResult(False, 1003, "unexpected_container")

        if ctx.audio_highest_seq < 0:
            ctx.audio_expected_seq = ctx.audio_seq
        seq = ctx.audio_seq
        ctx.audio_seq += 1

        if config.DIAG_AUDIO_GUARD and not ctx.diag_audio_seen:
            ctx.diag_audio_seen = True
            self._cancel_diag_timer(ctx)
            bus.publish({"type": "EVT_DIAG_FIRST_AUDIO_FRAME", "sid": ctx.sid})

        ctx.audio_chunks_recv += 1
        ctx.audio_bytes_recv += byte_count
        if ctx.audio_chunks_recv % 10 == 0:
            _log.info(
                "evt=ws_audio_ingress sid=%s chunks=%d bytes=%d",
                ctx.sid,
                ctx.audio_chunks_recv,
                ctx.audio_bytes_recv,
            )

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

        bucket: Optional[TokenBucket]
        if scope == "sid":
            bucket = ctx.sid_bucket
            message = "Too many concurrent connections."
        else:
            bucket = ctx.ip_bucket
            message = "Too many sessions from this network."

        retry_in_ms: Optional[int] = None
        if bucket is not None:
            delay = bucket.retry_after(1)
            if math.isfinite(delay) and delay >= 0:
                retry_in_ms = int(math.ceil(delay * 1000.0))

        await self._send_error(
            send,
            ctx.sid,
            "rate_limited",
            message,
            message=message,
            retryable=True,
            retry_in_ms=retry_in_ms,
        )
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
            if not ctx.asr_ready and not ALLOW_AUDIO_WITHOUT_ASR:
                return
            seq = ctx.audio_expected_seq
            chunk = ctx.audio_buffer.get(seq)
            if chunk is None:
                break
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
        if ctx.audio_chunks_recv == 1:
            self._emit_session_step(
                ctx.sid,
                "audio.stream_started",
                summary="Received first audio chunk",
                meta={"byte_count": byte_count},
                source="ws.audio",
            )
        self._handle_client_audio_activity(ctx)
        mask_phase = ctx.tts_mask_phase or "off"
        mask_open = mask_phase == "off"
        ready_gate = ctx.asr_ready or ALLOW_AUDIO_WITHOUT_ASR
        mic_open = ctx.client_mic_open
        send_open = not ctx.audio_send_closed
        backpressure_ok = ctx.backpressure_state != "on"

        gate_open = mask_open and ready_gate and mic_open and send_open and backpressure_ok
        if gate_open:
            _log.info(
                "evt=audio_ingress sid=%s len=%d gate_open=%s tts_mask=%s client_mic_open=%s audio_send_closed=%s backpressure_state=%s",
                ctx.sid,
                byte_count,
                gate_open,
                mask_phase,
                mic_open,
                ctx.audio_send_closed,
                ctx.backpressure_state,
            )
            bus.publish(
                {
                    "type": EVT_WS_AUDIO_SEND,
                    "sid": ctx.sid,
                    "chunk": bytes(chunk),
                    "len": byte_count,
                    "who": "server",
                    "source": "ws_server",
                }
            )
        else:
            if not mask_open:
                reason = "mask"
            elif not ready_gate:
                reason = "not_ready"
            elif not mic_open:
                reason = "mic_closed"
            elif not send_open:
                reason = "send_closed"
            else:
                reason = "backpressure"
            _log.info(
                "evt=audio_drop sid=%s len=%d reason=%s",
                ctx.sid,
                byte_count,
                reason,
            )
            return
        asr_runtime = getattr(self, "asr_runtime", None)
        if asr_runtime is not None:
            profile = ctx.audio_profile if isinstance(ctx.audio_profile, Mapping) else None
            fmt = str(profile.get("format") or "") if profile else ""
            codec = str(profile.get("codec") or "") if profile else ""
            if fmt == "pcm" or codec.startswith("pcm"):
                try:
                    asr_runtime.on_ws_audio(ctx.sid, chunk)
                except Exception:  # pragma: no cover - defensive logging
                    _log.exception("evt=ws_asr_audio_failed sid=%s", ctx.sid)
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

    def _log_speechmatics_control_summary(
        self, sid: str, payload: Mapping[str, Any]
    ) -> None:
        if not isinstance(payload, Mapping):
            return
        vendor = payload.get("vendor")
        if not (isinstance(vendor, str) and vendor.strip().lower() == "speechmatics"):
            return
        command_value = payload.get("command")
        if not isinstance(command_value, str):
            return
        command = command_value.strip().lower()
        if command == "start":
            audio_block = payload.get("audio_format")
            format_value: Optional[str] = None
            rate_value: Optional[int] = None
            channels_value: Optional[int] = None
            if isinstance(audio_block, Mapping):
                encoding = audio_block.get("encoding")
                if isinstance(encoding, str) and encoding:
                    normalized = encoding.strip()
                    if normalized.lower().startswith("pcm_") and len(normalized) > 4:
                        format_value = normalized.split("_", 1)[1]
                    else:
                        format_value = normalized
                format_field = audio_block.get("format")
                if isinstance(format_field, str) and format_field:
                    format_value = format_field.strip()
                sample_rate = (
                    audio_block.get("sample_rate")
                    or audio_block.get("rate")
                    or audio_block.get("rate_hz")
                )
                if isinstance(sample_rate, (int, float)):
                    try:
                        rate_value = int(sample_rate)
                    except (TypeError, ValueError, OverflowError):
                        rate_value = None
                channels = audio_block.get("channels")
                if isinstance(channels, (int, float)):
                    try:
                        channels_value = int(channels)
                    except (TypeError, ValueError, OverflowError):
                        channels_value = None
            if format_value is None:
                fallback_format = payload.get("format")
                if isinstance(fallback_format, str) and fallback_format:
                    format_value = fallback_format.strip()
            if rate_value is None:
                fallback_rate = (
                    payload.get("sample_rate")
                    or payload.get("rate")
                    or payload.get("rate_hz")
                )
                if isinstance(fallback_rate, (int, float)):
                    try:
                        rate_value = int(fallback_rate)
                    except (TypeError, ValueError, OverflowError):
                        rate_value = None
            if channels_value is None:
                fallback_channels = payload.get("channels")
                if isinstance(fallback_channels, (int, float)):
                    try:
                        channels_value = int(fallback_channels)
                    except (TypeError, ValueError, OverflowError):
                        channels_value = None
            normalized_format = (format_value or "s16le")
            normalized_rate = rate_value if rate_value is not None else 16000
            normalized_channels = channels_value if channels_value is not None else 1
            _log.info(
                "evt=ws_json_send_summary sid=%s kind=start vendor=speechmatics format=%s rate=%d channels=%d",
                sid,
                normalized_format,
                normalized_rate,
                normalized_channels,
            )
            bus.publish(
                {
                    "type": EVT_WS_JSON_SEND_SUMMARY,
                    "sid": sid,
                    "who": "server",
                    "source": "ws.adapter",
                    "meta": {
                        "kind": "start",
                        "vendor": "speechmatics",
                        "format": normalized_format,
                        "rate": normalized_rate,
                        "channels": normalized_channels,
                    },
                }
            )
        elif command == "stop":
            _log.info(
                "evt=ws_json_send_summary sid=%s kind=stop vendor=speechmatics",
                sid,
            )
            bus.publish(
                {
                    "type": EVT_WS_JSON_SEND_SUMMARY,
                    "sid": sid,
                    "who": "server",
                    "source": "ws.adapter",
                    "meta": {
                        "kind": "stop",
                        "vendor": "speechmatics",
                    },
                }
            )
        elif command in {"ping", "pong"}:
            _log.info(
                "evt=ws_json_send_summary sid=%s kind=%s vendor=speechmatics",
                sid,
                command,
            )

    async def _send_json(
        self,
        send: Callable[[dict], Awaitable[None]],
        sid: str,
        payload: Dict[str, Any],
    ) -> None:
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
        meta["ws"]["from_adapter"] = True
        preview = self._make_preview_from_bytes(payload_bytes)
        if preview is not None:
            meta["ws"]["preview"] = preview
        try:
            parsed_frame = json.loads(text)
        except json.JSONDecodeError:
            parsed_frame = None
        frame_payload: Dict[str, Any]
        if isinstance(parsed_frame, Mapping):
            frame_payload = dict(parsed_frame)
        else:
            frame_payload = dict(payload)
        self._log_speechmatics_control_summary(sid, payload)
        await self._publish(EVT_WS_JSON_SEND, sid, meta, frame_payload)

    async def _send_error(
        self,
        send: Callable[[dict], Awaitable[None]],
        sid: str,
        code: str,
        detail: str,
        *,
        message: Optional[str] = None,
        retryable: Optional[bool] = None,
        retry_in_ms: Optional[int] = None,
    ) -> None:
        payload: Dict[str, Any] = {"type": "error", "code": code}
        if detail:
            payload["detail"] = detail
        if message:
            payload["message"] = message
        if retryable is not None:
            payload["retryable"] = bool(retryable)
        if retry_in_ms is not None:
            payload["retry_in_ms"] = int(retry_in_ms)
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
                    now_ms = int(time.time() * 1000)
                    last_activity = ctx.last_client_activity_ms or 0
                    last_pong = ctx.last_client_pong_ms or 0
                    last_ping = ctx.last_server_ping_ms or 0
                    timeout_threshold = now_ms - _HEARTBEAT_TIMEOUT_MS
                    activity_missed = bool(last_activity) and last_activity <= timeout_threshold
                    pong_missed = False
                    if last_ping and last_ping <= timeout_threshold:
                        pong_missed = last_pong == 0 or last_pong < last_ping
                    if activity_missed or pong_missed:
                        reason = "no_client_activity" if activity_missed else "no_client_pong"
                        _log.warning(
                            "evt=ws_heartbeat_missed sid=%s last_client_ms=%s last_client_pong_ms=%s last_server_ping_ms=%s reason=%s",
                            ctx.sid,
                            last_activity,
                            last_pong,
                            last_ping,
                            reason,
                        )
                        await send(
                            {
                                "type": "websocket.close",
                                "code": 1001,
                                "reason": "heartbeat_timeout",
                            }
                        )
                        return
                    ping_frame = {"type": "server.ping", "ts": now_ms}
                    ctx.last_server_ping_ms = now_ms
                    await self._send_json(send, ctx.sid, ping_frame)
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - defensive
                _log.exception("evt=ws_keepalive_failed sid=%s", ctx.sid)
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
            meta_obj = event.get("meta")
            if isinstance(meta_obj, Mapping):
                ws_meta = meta_obj.get("ws")
                if isinstance(ws_meta, Mapping) and ws_meta.get("from_adapter"):
                    return
            payload = self._extract_outbound_payload(ctx, event)
            if payload is None:
                return

            def _on_loop() -> None:
                frame_type = payload.get("type")
                if frame_type == "tts.end":
                    self._handle_tts_end_diag(ctx, loop, payload)
                    req_val = payload.get("req_id") if isinstance(payload, dict) else None
                    req_value = req_val if isinstance(req_val, str) and req_val else None
                    if req_value:
                        ctx.last_tts_end_req_id = req_value
                        ctx.await_user_req_id = req_value
                    elif isinstance(ctx.await_user_req_id, str) and ctx.await_user_req_id:
                        ctx.last_tts_end_req_id = ctx.await_user_req_id
                    policy = self._policy_snapshot()
                    self._maybe_emit_await_user(ctx, policy)
                    _schedule_listen_handoff(req_value)
                _enqueue(payload)

            try:
                loop.call_soon_threadsafe(_on_loop)
            except RuntimeError:
                pass

        ctx.subscription_token = bus.subscribe(EVT_WS_JSON_SEND, _handle_event)

        def _handle_asr_event(event: dict) -> None:
            if event.get("sid") != ctx.sid or ctx.outbox is None:
                return

            def _on_loop() -> None:
                frame = self._coerce_asr_frame(ctx, event)
                if frame is None:
                    return
                _enqueue(frame)

            try:
                loop.call_soon_threadsafe(_on_loop)
            except RuntimeError:
                pass

        ctx.asr_partial_subscription_token = bus.subscribe(
            EVT_ASR_PARTIAL, _handle_asr_event
        )
        ctx.asr_final_subscription_token = bus.subscribe(
            EVT_ASR_FINAL, _handle_asr_event
        )

        def _handle_asr_unavailable_event(event: dict) -> None:
            if event.get("type") != "asr.unavailable":
                return
            if event.get("sid") != ctx.sid or ctx.outbox is None:
                return

            def _on_loop() -> None:
                ctx.asr_ready = False
                ctx.awaiting_asr_ready = False
                ctx.client_capture_armed = False
                payload: Dict[str, Any] = {"type": "asr.unavailable", "sid": ctx.sid}
                reason = event.get("reason")
                if isinstance(reason, str) and reason:
                    payload["reason"] = reason
                details = event.get("details")
                detail_text = ""
                if details is not None:
                    detail_text = details if isinstance(details, str) else str(details)
                    payload["details"] = detail_text
                else:
                    detail_text = ""
                lower_reason = reason.lower() if isinstance(reason, str) else ""
                lower_detail = detail_text.lower()
                concurrency_likely = False
                if ctx.asr_vendor == "speechmatics":
                    if "concurrent_session" in lower_detail:
                        concurrency_likely = True
                    elif "concurrent_session" in lower_reason:
                        concurrency_likely = True
                if concurrency_likely:
                    ctx.asr_recovering_until = time.monotonic() + 3.0
                    ctx.asr_recovering_reason = "concurrent_session"
                    ctx.asr_recovering_audio_logged = False
                    ctx.client_capture_armed = True
                else:
                    ctx.asr_recovering_until = 0.0
                    ctx.asr_recovering_reason = None
                    ctx.asr_recovering_audio_logged = False
                _enqueue(payload)

            try:
                loop.call_soon_threadsafe(_on_loop)
            except RuntimeError:
                pass

        ctx.asr_unavailable_subscription_token = bus.subscribe(
            "asr.unavailable", _handle_asr_unavailable_event
        )

        async def _perform_listen_handoff(req_id: str) -> None:
            key = self._turn_key(ctx, req_id)
            runtime = getattr(self, "asr_runtime", None)
            if runtime is None:
                self._clear_pending_for_key(ctx, key)
                return
            open_if_needed = getattr(runtime, "open_if_needed", None)
            should_wait_for_ready = callable(open_if_needed)

            if ctx.tts_mask_phase != "off":
                self._set_after_mask_for_key(ctx, key)
                self._clear_pending_for_key(ctx, key)
                _log.info(
                    "evt=listen_handoff_aborted reason=mask_on sid=%s req_id=%s",
                    ctx.sid,
                    req_id,
                )
                return

            if callable(open_if_needed):
                try:
                    await open_if_needed(ctx.sid, req_id=req_id)
                except asyncio.CancelledError:
                    self._clear_pending_for_key(ctx, key)
                    raise
                except Exception:  # pragma: no cover - defensive logging
                    self._clear_pending_for_key(ctx, key)
                    _log.exception("evt=listen_handoff_open_failed sid=%s req_id=%s", ctx.sid, req_id)
                    return

            if should_wait_for_ready:
                deadline = time.monotonic() + 1.0
                while not ctx.asr_ready and time.monotonic() < deadline:
                    if ctx.tts_mask_phase != "off":
                        self._set_after_mask_for_key(ctx, key)
                        self._clear_pending_for_key(ctx, key)
                        _log.info(
                            "evt=listen_handoff_aborted reason=mask_on sid=%s req_id=%s",
                            ctx.sid,
                            req_id,
                        )
                        return
                    await asyncio.sleep(0.01)

                if not ctx.asr_ready:
                    _log.warning("evt=listen_handoff_asr_not_ready sid=%s req_id=%s", ctx.sid, req_id)

            if ctx.outbox is None:
                self._clear_pending_for_key(ctx, key)
                return

            if ctx.tts_mask_phase != "off":
                self._set_after_mask_for_key(ctx, key)
                self._clear_pending_for_key(ctx, key)
                _log.info(
                    "evt=listen_handoff_aborted reason=mask_on sid=%s req_id=%s",
                    ctx.sid,
                    req_id,
                )
                return

            mode = ctx.audio_pipeline_mode or "pcm16"
            descriptor = dict(self._input_descriptor_for_mode(mode))
            timeslice_ms = self._resolve_capture_timeslice(ctx, mode)
            ready_frame = {
                "type": "asr.ready",
                "input": dict(descriptor),
            }
            if isinstance(ctx.asr_vendor, str) and ctx.asr_vendor:
                ready_frame["vendor"] = ctx.asr_vendor
            capture = dict(descriptor)
            capture["timeslice_ms"] = timeslice_ms
            capture["manual_gate"] = False
            session_policy = ctx.session_capture_policy or self._session_capture_policy_for_mode(mode)
            input_start = {
                "type": "input.start",
                "capture": capture,
            }
            if session_policy:
                input_start["policy"] = session_policy

            _enqueue(ready_frame)
            _enqueue(input_start)
            ctx.ingress_packets = 0
            ctx.ingress_bytes = 0
            ctx.first_ingress_ms = None
            mic_armed_now = int(time.time() * 1000)
            ctx.mic_armed_ms = mic_armed_now
            ctx.asr_ready_bundle_sent_ms = mic_armed_now
            ctx.awaiting_asr_ready = True
            ctx.client_capture_armed = True
            start_payload: Dict[str, Any] = {"type": "start_listening"}
            if session_policy:
                start_payload["policy"] = session_policy
            _enqueue(start_payload)

            if key is not None:
                ctx.listen_handoff_done.add(key)
            self._clear_pending_for_key(ctx, key)
            self._clear_after_mask_for_key(ctx, key)
            current_key = self._turn_key(ctx, ctx.await_user_req_id)
            if current_key == key:
                ctx.await_user_expected = False
                ctx.await_user_req_id = None
            if ctx.last_tts_end_req_id == req_id:
                ctx.last_tts_end_req_id = None
            ctx.client_mic_open = False
            ctx.mic_nudge_sent = False

            self._emit_hud_state(ctx, "Listening")
            self._schedule_mic_open_guard(ctx, loop)
            _log.info(
                "evt=listen_handoff_ready sid=%s req_id=%s input.mode=%s input.mime=%s",
                ctx.sid,
                req_id,
                descriptor.get("mode", ""),
                descriptor.get("mime", ""),
            )

        def _schedule_listen_handoff(trigger_req_id: Optional[str]) -> None:
            async def _attempt() -> None:
                async with ctx.listen_lock:
                    if not ctx.await_user_expected:
                        return
                    candidate = trigger_req_id if isinstance(trigger_req_id, str) and trigger_req_id else ctx.await_user_req_id
                    if not isinstance(candidate, str) or not candidate:
                        return
                    if ctx.last_tts_end_req_id != candidate:
                        return
                    key = self._turn_key(ctx, candidate)
                    if key is None:
                        return
                    if key in ctx.listen_handoff_done:
                        _log.info(
                            "evt=listen_handoff_skip already_done sid=%s req_id=%s",
                            ctx.sid,
                            candidate,
                        )
                        return
                    if ctx.outbox is None:
                        return
                    existing = ctx.listen_handoff_task
                    if existing is not None and not existing.done():
                        return
                    if not ctx.await_user_pending or ctx.await_user_pending_key != key:
                        initiated = self._initiate_listen_handoff(ctx, candidate)
                        if not initiated:
                            return
                    if ctx.tts_mask_phase != "off":
                        self._set_after_mask_for_key(ctx, key)
                        return
                    self._clear_after_mask_for_key(ctx, key)

                    async def _run() -> None:
                        try:
                            await _perform_listen_handoff(candidate)
                        finally:
                            if ctx.listen_handoff_task_key == key:
                                ctx.listen_handoff_task = None
                                ctx.listen_handoff_task_key = None

                    ctx.listen_handoff_task = asyncio.create_task(_run())
                    ctx.listen_handoff_task_key = key
                    _log.info(
                        "evt=listen_handoff_scheduled sid=%s req_id=%s",
                        ctx.sid,
                        candidate,
                    )

            try:
                loop.create_task(_attempt())
            except RuntimeError:
                pass

        def _handle_asr_open_event(event: dict) -> None:
            if event.get("sid") != ctx.sid:
                return

            def _on_loop() -> None:
                if ctx.await_user_pending:
                    _schedule_listen_handoff(None)

            try:
                loop.call_soon_threadsafe(_on_loop)
            except RuntimeError:
                pass

        ctx.asr_open_subscription_token = bus.subscribe(EVT_ASR_OPEN, _handle_asr_open_event)

        def _handle_tts_end_event(event: dict) -> None:
            if event.get("sid") != ctx.sid:
                return
            req_id = event.get("req_id")
            req_value = req_id if isinstance(req_id, str) and req_id else None

            def _on_loop() -> None:
                if req_value:
                    ctx.last_tts_end_req_id = req_value
                    ctx.await_user_req_id = req_value
                elif isinstance(ctx.await_user_req_id, str) and ctx.await_user_req_id:
                    ctx.last_tts_end_req_id = ctx.await_user_req_id
                policy = self._policy_snapshot()
                self._maybe_emit_await_user(ctx, policy)
                _schedule_listen_handoff(req_value)

            try:
                loop.call_soon_threadsafe(_on_loop)
            except RuntimeError:
                pass

        ctx.tts_end_subscription_token = bus.subscribe(EVT_TTS_END, _handle_tts_end_event)

        def _handle_tts_mask_event(event: dict) -> None:
            if event.get("sid") != ctx.sid:
                return

            raw_phase = event.get("phase")
            if not isinstance(raw_phase, str):
                return

            stripped = raw_phase.strip()
            normalized = stripped.lower()
            phase_value = normalized or stripped or raw_phase

            def _on_loop() -> None:
                ctx.tts_mask_phase = phase_value
                if ctx.tts_mask_phase != "off":
                    ctx.await_user_after_mask = False
                    ctx.await_user_after_mask_key = None
                    ctx.client_mic_open = False
                    ctx.hud_state = None
                    ctx.mic_nudge_sent = False
                    self._cancel_mic_open_timer(ctx)
                    return
                _schedule_listen_handoff(None)

            try:
                loop.call_soon_threadsafe(_on_loop)
            except RuntimeError:
                pass

        ctx.mask_subscription_token = bus.subscribe(EVT_TTS_MASK, _handle_tts_mask_event)

        def _handle_turn_state_event(event: dict) -> None:
            if event.get("type") != "EVT_TURN_STATE":
                return
            if event.get("sid") != ctx.sid:
                return

            raw_meta = event.get("meta")
            if isinstance(raw_meta, Mapping):
                meta = {key: value for key, value in dict(raw_meta).items() if key}
            else:
                meta = {}

            state = meta.get("state", event.get("state"))
            reason = meta.get("reason", event.get("reason"))
            state_text = state if isinstance(state, str) else None
            reason_text = reason if isinstance(reason, str) else None

            def _on_loop() -> None:
                frame: Dict[str, Any] = {
                    "type": "turn.state",
                    "state": state,
                    "reason": reason,
                    "meta": meta,
                }
                _enqueue(frame)
                if state_text == "Ready" and reason_text == "tts_end":
                    policy = self._policy_snapshot()
                    self._maybe_emit_await_user(ctx, policy)

            try:
                loop.call_soon_threadsafe(_on_loop)
            except RuntimeError:
                _on_loop()

        ctx.turn_state_subscription_token = bus.subscribe(
            "EVT_TURN_STATE", _handle_turn_state_event
        )

        def _handle_audio_event(event: dict) -> None:
            if event.get("sid") != ctx.sid:
                return
            if ctx.audio_send_closed:
                return
            source = event.get("source")
            if source == "ws_server":
                meta = event.get("meta")
                audio_meta = meta.get("audio") if isinstance(meta, Mapping) else None
                if not isinstance(audio_meta, Mapping):
                    _log.debug(
                        "evt=audio_event_ignored sid=%s source=%s", ctx.sid, source
                    )
                    return
            chunk = event.get("chunk")
            if isinstance(chunk, (bytes, bytearray, memoryview)):
                chunk_bytes = bytes(chunk)
            else:
                return

            def _deliver() -> None:
                async def _run_audio_task() -> None:
                    start = time.perf_counter()
                    try:
                        await self._send_audio_frame(ctx, send, chunk_bytes)
                    except asyncio.CancelledError:
                        return
                    except ClientDisconnected:
                        self._disable_audio_send(ctx, "client_disconnected")
                    except Exception:  # pragma: no cover - defensive logging
                        self._disable_audio_send(ctx, "send_failed")
                        _log.exception("evt=ws_audio_chunk_failed sid=%s", ctx.sid)
                    finally:
                        duration_ms = max(0, int((time.perf_counter() - start) * 1000))
                        current = asyncio.current_task()
                        if current is not None:
                            ctx.audio_tasks.discard(current)
                        _log.info(
                            "evt=audio_task_complete sid=%s duration_ms=%d",
                            ctx.sid,
                            duration_ms,
                        )

                task = asyncio.create_task(_run_audio_task())
                ctx.audio_tasks.add(task)

            try:
                loop.call_soon_threadsafe(_deliver)
            except RuntimeError:
                pass

        _handle_audio_event._telemetry_accepts_binary = True  # type: ignore[attr-defined]
        ctx.audio_subscription_token = bus.subscribe(EVT_WS_AUDIO_SEND, _handle_audio_event)
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

    def _coerce_asr_frame(
        self, ctx: AdapterContext, event: Mapping[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(event, Mapping):
            return None

        event_type = event.get("type")
        if event_type == EVT_ASR_PARTIAL:
            frame_type = "asr.partial"
        elif event_type == EVT_ASR_FINAL:
            frame_type = "asr.final"
        else:
            return None

        source = event.get("source")
        if isinstance(source, str) and source == "speechmatics_client":
            vendor = event.get("vendor")
            if not isinstance(vendor, str) or vendor.lower() != "speechmatics":
                return None

        raw_meta = event.get("meta")
        meta = raw_meta if isinstance(raw_meta, Mapping) else None
        text = event.get("text")
        if not isinstance(text, str) or not text.strip():
            if meta is not None:
                candidate_text = meta.get("text")
                if isinstance(candidate_text, str):
                    text = candidate_text
        if not isinstance(text, str) or not text.strip():
            return None

        frame: Dict[str, Any] = {"type": frame_type, "text": text}

        req_id = event.get("req_id")
        if not isinstance(req_id, str) or not req_id.strip():
            if meta is not None:
                meta_req = meta.get("req_id")
                if isinstance(meta_req, str) and meta_req.strip():
                    req_id = meta_req
                else:
                    req_id = None
        else:
            req_id = req_id.strip()
        if isinstance(req_id, str) and req_id:
            frame["req_id"] = req_id

        confidence = event.get("confidence")
        if not isinstance(confidence, (int, float)) and meta is not None:
            meta_conf = meta.get("confidence")
            if isinstance(meta_conf, (int, float)):
                confidence = float(meta_conf)
        if isinstance(confidence, (int, float)):
            frame["confidence"] = float(confidence)

        vendor = event.get("vendor")
        if not isinstance(vendor, str) or not vendor:
            if meta is not None:
                meta_vendor = meta.get("vendor")
                if isinstance(meta_vendor, str) and meta_vendor:
                    vendor = meta_vendor
                else:
                    vendor = None
        if isinstance(vendor, str) and vendor:
            frame["vendor"] = vendor.lower() if vendor.lower() == "speechmatics" else vendor

        if meta is not None:
            partial_seq = meta.get("partial_seq")
            if isinstance(partial_seq, int):
                frame["partial_seq"] = partial_seq

        return frame

    def _start_asr_ready_tracker(
        self, ctx: AdapterContext, telemetry_bus: Optional[Any] = None
    ) -> None:
        if ctx.asr_subscription_token is not None:
            return

        if telemetry_bus is None:
            telemetry_bus = bus

        ctx.asr_subscription_bus = telemetry_bus
        _log.info("evt=asr_ready_tracker_start sid=%s", ctx.sid)
        self._emit_session_step(
            ctx.sid,
            "asr.ready_tracker_started",
            summary="Subscribed to ASR ready events",
            source="ws.asr",
        )

        loop = asyncio.get_running_loop()
        loop_thread = threading.current_thread()

        def _handle(event: dict) -> None:
            if event.get("type") != EVT_ASR_READY:
                return
            if event.get("sid") != ctx.sid:
                return
            if ctx.asr_ready:
                return

            ctx.asr_ready = True
            ctx.awaiting_asr_ready = False
            ctx.asr_recovering_until = 0.0
            ctx.asr_recovering_reason = None
            ctx.asr_recovering_audio_logged = False
            ctx.client_capture_armed = True

            frame: Dict[str, Any] = {"type": "asr.ready"}
            vendor = event.get("vendor")
            if isinstance(vendor, str) and vendor:
                frame["vendor"] = vendor

            def _publish_ready() -> None:
                try:
                    asyncio.create_task(self._flush_audio_buffer(ctx))
                except RuntimeError:
                    pass
                telemetry_bus.publish(
                    {
                        "type": EVT_WS_JSON_SEND,
                        "sid": ctx.sid,
                        "who": "server",
                        "source": "ws_server",
                        "meta": {"ws": {"from_adapter": True}},
                        "frame": frame,
                        "payload": frame,
                    }
                )

            if threading.current_thread() is loop_thread:
                _publish_ready()
                return

            try:
                loop.call_soon_threadsafe(_publish_ready)
            except RuntimeError:
                _publish_ready()

        ctx.asr_subscription_token = telemetry_bus.subscribe(EVT_ASR_READY, _handle)

    def _extract_outbound_payload(
        self, ctx: AdapterContext, event: dict
    ) -> Optional[Dict[str, Any]]:
        payload = self._coerce_payload(event.get("payload"))
        if payload is None:
            payload = self._coerce_payload(event.get("frame"))

        if payload is None:
            meta = event.get("meta")
            if isinstance(meta, dict):
                ws_meta = meta.get("ws")
                if isinstance(ws_meta, dict):
                    payload = self._coerce_payload(ws_meta.get("frame"))
                    if payload is None:
                        payload = self._coerce_payload(ws_meta.get("preview"))

        if payload is None:
            return None

        frame_type = payload.get("type") if isinstance(payload, dict) else None
        if frame_type == "policy.interaction" and isinstance(payload, dict):
            prev_req_id = ctx.await_user_req_id
            prev_key = self._turn_key(ctx, prev_req_id)
            ctx.await_user_expected = self._policy_requests_listen(payload)
            req_id_value = payload.get("req_id") if isinstance(payload, dict) else None
            new_req_id = req_id_value if isinstance(req_id_value, str) and req_id_value else None
            if new_req_id is None and isinstance(payload, dict):
                interaction_id = payload.get("interaction_id")
                if isinstance(interaction_id, str):
                    trimmed = interaction_id.strip()
                    if trimmed:
                        new_req_id = trimmed
            ctx.await_user_req_id = new_req_id
            ctx.await_user_cue_emitted = False
            new_key = self._turn_key(ctx, new_req_id)
            if prev_key is not None and prev_key != new_key:
                task = ctx.listen_handoff_task
                if (
                    task is not None
                    and not task.done()
                    and ctx.listen_handoff_task_key == prev_key
                ):
                    task.cancel()

                    def _suppress_cancel(done: asyncio.Task[None]) -> None:
                        try:
                            done.result()
                        except asyncio.CancelledError:
                            pass
                        except Exception:  # pragma: no cover - defensive logging
                            _log.exception(
                                "evt=listen_handoff_cancel_error sid=%s", ctx.sid
                            )

                    task.add_done_callback(_suppress_cancel)
                    ctx.listen_handoff_task = None
                    ctx.listen_handoff_task_key = None
                self._clear_pending_for_key(ctx, prev_key)
                self._clear_after_mask_for_key(ctx, prev_key)
            if ctx.await_user_expected:
                ctx.await_user_pending = False
                ctx.await_user_pending_key = None
                ctx.await_user_after_mask = False
                ctx.await_user_after_mask_key = None
                ctx.last_tts_end_req_id = None
            else:
                ctx.await_user_pending = False
                ctx.await_user_pending_key = None
                ctx.await_user_after_mask = False
                ctx.await_user_after_mask_key = None
                ctx.last_tts_end_req_id = None
                ctx.mic_nudge_sent = False
                self._cancel_mic_open_timer(ctx)
                ctx.await_user_cue_emitted = False
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
            meta = {}
            source_meta = normalized.get("meta")
            if isinstance(source_meta, dict):
                meta = dict(source_meta)
            meta["sid"] = ctx.sid
            normalized["meta"] = meta
            policy_payload = normalized.get("policy")
            if isinstance(policy_payload, dict):
                ctx.policy_snapshot = policy_payload
                ctx.allowed_asr_vendors = self._allowed_asr_vendors()
                snapshot_mapping = policy_payload
                selected_vendor, _ = self._select_asr_vendor(
                    ctx,
                    snapshot_mapping,
                    ctx.allowed_asr_vendors,
                    log_selection=not ctx.asr_vendor_logged,
                )
                ctx.asr_vendor = selected_vendor or "speechmatics"
                ctx.audio_pipeline_mode = self._resolve_audio_pipeline_mode(
                    snapshot_mapping
                )
                if ctx.asr_vendor == "speechmatics":
                    ctx.audio_pipeline_mode = "pcm16"
                mode = ctx.audio_pipeline_mode or "pcm16"
                ctx.session_capture_policy = self._session_capture_policy_for_mode(mode)
        return normalized

    @staticmethod
    def _truncate_banner_string(value: str, limit: int = 240) -> str:
        if not isinstance(value, str):
            return ""
        if limit <= 0:
            return ""
        if len(value) <= limit:
            return value
        return value[: limit - 1] + "\u2026"

    @staticmethod
    def _sanitize_banner_value(value: Any, depth: int = 0) -> Any:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            if isinstance(value, float) and not math.isfinite(value):
                return None
            return value
        if isinstance(value, str):
            return ChatV2Adapter._truncate_banner_string(value)
        if depth >= 2:
            return None
        if isinstance(value, dict):
            sanitized: Dict[str, Any] = {}
            for index, (key, inner) in enumerate(value.items()):
                if index >= 16:
                    break
                if not isinstance(key, str) or not key:
                    continue
                inner_sanitized = ChatV2Adapter._sanitize_banner_value(inner, depth + 1)
                if inner_sanitized is None:
                    continue
                sanitized[ChatV2Adapter._truncate_banner_string(key, 48)] = inner_sanitized
            return sanitized
        if isinstance(value, (list, tuple, set)):
            sanitized_list = []
            for item in value:
                if len(sanitized_list) >= 8:
                    break
                inner_sanitized = ChatV2Adapter._sanitize_banner_value(item, depth + 1)
                if inner_sanitized is None:
                    continue
                sanitized_list.append(inner_sanitized)
            return sanitized_list
        return None

    @staticmethod
    def _sanitize_client_banner_info(payload: Any) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        sanitized = ChatV2Adapter._sanitize_banner_value(payload)
        return sanitized if isinstance(sanitized, dict) else {}

    @staticmethod
    def _sanitize_client_banner_event(payload: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(payload, dict):
            return None
        label = payload.get("label") or payload.get("event") or payload.get("reason")
        if not isinstance(label, str) or not label.strip():
            return None
        normalized: Dict[str, Any] = {
            "label": ChatV2Adapter._truncate_banner_string(label.strip(), 64)
        }
        ts_value = payload.get("ts_ms")
        if isinstance(ts_value, (int, float)) and math.isfinite(ts_value):
            normalized["ts_ms"] = int(ts_value)
        else:
            alt_ts = payload.get("ts")
            if isinstance(alt_ts, (int, float)) and math.isfinite(alt_ts):
                normalized["ts_ms"] = int(alt_ts)
        meta_payload = payload.get("meta")
        sanitized_meta = ChatV2Adapter._sanitize_banner_value(meta_payload)
        if isinstance(sanitized_meta, dict) and sanitized_meta:
            normalized["meta"] = sanitized_meta
        elif isinstance(sanitized_meta, list) and sanitized_meta:
            normalized["meta"] = sanitized_meta
        if "ts_ms" not in normalized:
            normalized["ts_ms"] = int(time.time() * 1000)
        return normalized

    @staticmethod
    def _sanitize_client_log(payload: Any) -> Dict[str, Any]:
        if not isinstance(payload, Mapping):
            return {}

        sanitized: Dict[str, Any] = {}

        label = payload.get("label")
        if isinstance(label, str) and label.strip():
            sanitized["label"] = label.strip()[:64]

        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            sanitized["message"] = message.strip()[:256]

        client_ts = payload.get("ts") or payload.get("client_ts")
        if isinstance(client_ts, (int, float)) and math.isfinite(client_ts):
            sanitized["client_ts_ms"] = int(client_ts)

        detail = payload.get("detail")
        if detail is not None:
            try:
                sanitized["detail"] = bus.redact_payload(detail)
            except Exception:
                try:
                    sanitized["detail"] = bus.redact_payload(str(detail))
                except Exception:
                    sanitized["detail"] = str(detail)

        extra = payload.get("extra")
        if extra is not None:
            try:
                sanitized["extra"] = bus.redact_payload(extra)
            except Exception:
                sanitized["extra"] = str(extra)

        return sanitized

    @staticmethod
    def _sanitize_autostart_meta(raw_meta: Any) -> Dict[str, Any]:
        if not isinstance(raw_meta, Mapping):
            return {}

        sanitized: Dict[str, Any] = {}
        count = 0
        for key, value in raw_meta.items():
            if count >= 8:
                break
            if not isinstance(key, str):
                continue
            trimmed_key = key.strip()
            if not trimmed_key:
                continue
            truncated_key = trimmed_key[:48]
            if isinstance(value, str):
                trimmed_value = value.strip()
                if not trimmed_value:
                    continue
                sanitized[truncated_key] = trimmed_value[:120]
            elif isinstance(value, bool):
                sanitized[truncated_key] = value
            elif isinstance(value, (int, float)) and math.isfinite(value):
                sanitized[truncated_key] = value
            else:
                continue
            count += 1

        return sanitized

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
    def _policy_requests_listen(frame: Dict[str, Any]) -> bool:
        actions = frame.get("actions") if isinstance(frame, dict) else None
        if isinstance(actions, list):
            for action in actions:
                if isinstance(action, str) and action.strip() == "assistant.await_user":
                    return True
        return False

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
                    await self._send_outbound_frame(ctx, send, payload)
                except asyncio.CancelledError:
                    raise
                except ClientDisconnected:
                    _log.info(
                        "evt=ws_outbound_client_disconnect sid=%s phase=frame",
                        ctx.sid,
                    )
                    break
                except Exception:  # pragma: no cover - defensive
                    _log.exception("evt=ws_outbound_frame_failed sid=%s", ctx.sid)
                finally:
                    queue.task_done()
        except asyncio.CancelledError:
            raise
        finally:
            ctx.outbox = None

    async def _send_outbound_frame(
        self,
        ctx: AdapterContext,
        send: Callable[[dict], Awaitable[None]],
        payload: Dict[str, Any],
    ) -> None:
        if (
            isinstance(payload, dict)
            and "message" in payload
            and isinstance(ctx.asr_vendor, str)
            and ctx.asr_vendor.lower() == "speechmatics"
        ):
            return
        lock = self._ensure_send_lock(ctx)
        async with lock:
            frame_type = payload.get("type")
            if frame_type in {"asr.partial", "asr.final"}:
                vendor = payload.get("vendor")
                if isinstance(vendor, str) and vendor.lower() == "speechmatics":
                    log_event = (
                        "evt=asr_partial_sent"
                        if frame_type == "asr.partial"
                        else "evt=asr_final_sent"
                    )
                    text_value = payload.get("text")
                    text_len = len(text_value) if isinstance(text_value, str) else 0
                    _log.info("%s vendor=speechmatics chars=%d", log_event, text_len)
            await self._send_json(send, ctx.sid, payload)

    async def _send_audio_frame(
        self,
        ctx: AdapterContext,
        send: Callable[[dict], Awaitable[None]],
        chunk: bytes,
    ) -> None:
        lock = self._ensure_send_lock(ctx)
        async with lock:
            try:
                await send({"type": "websocket.send", "bytes": chunk})
            except ClientDisconnected:
                _log.info(
                    "evt=ws_outbound_client_disconnect sid=%s phase=audio",
                    ctx.sid,
                )
                self._disable_audio_send(ctx, "client_disconnected")
            except Exception:
                self._disable_audio_send(ctx, "send_failed")
                raise

    def _disable_audio_send(self, ctx: AdapterContext, reason: str) -> None:
        if ctx.audio_send_closed:
            return
        ctx.audio_send_closed = True
        _log.info("evt=ws_audio_send_disabled sid=%s reason=%s", ctx.sid, reason)

        token = ctx.audio_subscription_token
        ctx.audio_subscription_token = None
        if token:
            try:
                bus.unsubscribe(token)
            except Exception:  # pragma: no cover - defensive
                _log.debug(
                    "evt=ws_audio_send_unsubscribe_failed sid=%s", ctx.sid, exc_info=True
                )

        pending = list(ctx.audio_tasks)
        ctx.audio_tasks.clear()
        for task in pending:
            task.cancel()

    async def _cleanup_outbound(self, ctx: AdapterContext) -> None:
        self._cancel_diag_timer(ctx)
        self._cancel_mic_open_timer(ctx)
        token = ctx.subscription_token
        ctx.subscription_token = None
        if token:
            bus.unsubscribe(token)

        audio_token = ctx.audio_subscription_token
        ctx.audio_subscription_token = None
        if audio_token:
            bus.unsubscribe(audio_token)

        unavailable_token = ctx.asr_unavailable_subscription_token
        ctx.asr_unavailable_subscription_token = None
        if unavailable_token:
            bus.unsubscribe(unavailable_token)

        partial_token = ctx.asr_partial_subscription_token
        ctx.asr_partial_subscription_token = None
        if partial_token:
            bus.unsubscribe(partial_token)

        final_token = ctx.asr_final_subscription_token
        ctx.asr_final_subscription_token = None
        if final_token:
            bus.unsubscribe(final_token)

        mask_token = ctx.mask_subscription_token
        ctx.mask_subscription_token = None
        if mask_token:
            bus.unsubscribe(mask_token)

        turn_state_token = ctx.turn_state_subscription_token
        ctx.turn_state_subscription_token = None
        if turn_state_token:
            bus.unsubscribe(turn_state_token)

        tts_end_token = ctx.tts_end_subscription_token
        ctx.tts_end_subscription_token = None
        if tts_end_token:
            bus.unsubscribe(tts_end_token)

        task = ctx.outbound_task
        ctx.outbound_task = None
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        ctx.partial_coalescer.cancel()

        pending_handoff = ctx.listen_handoff_task
        ctx.listen_handoff_task = None
        ctx.listen_handoff_task_key = None
        if pending_handoff is not None:
            pending_handoff.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pending_handoff

        pending_audio = list(ctx.audio_tasks)
        if pending_audio:
            for task in pending_audio:
                task.cancel()
            for task in pending_audio:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            completed = sum(1 for task in pending_audio if task.done())
            remaining = len(pending_audio) - completed
        else:
            completed = 0
            remaining = 0
        ctx.audio_tasks.clear()
        _log.info(
            "evt=audio_task_shutdown_drain sid=%s completed=%d remaining=%d",
            ctx.sid,
            completed,
            remaining,
        )

        ctx.outbox = None
        ctx.await_user_expected = False
        ctx.await_user_pending = False
        ctx.await_user_pending_key = None
        ctx.await_user_after_mask = False
        ctx.await_user_after_mask_key = None
        ctx.await_user_req_id = None
        ctx.last_tts_end_req_id = None
        ctx.await_user_cue_emitted = False

    def _handle_tts_end_diag(
        self,
        ctx: AdapterContext,
        loop: asyncio.AbstractEventLoop,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not config.DIAG_AUDIO_GUARD:
            return
        req_id = None
        if isinstance(payload, dict):
            candidate = payload.get("req_id")
            if isinstance(candidate, str) and candidate:
                req_id = candidate
        key = self._turn_key(ctx, req_id)
        ctx.tts_end_ts = time.monotonic()
        ctx.diag_audio_seen = False
        self._cancel_diag_timer(ctx)
        ctx.diag_timer_key = key
        try:
            ctx.diag_timer = loop.call_later(
                _DIAG_NO_AUDIO_CHECK_DELAY_SECONDS,
                self._emit_no_audio_diag,
                ctx,
                loop,
                key,
            )
        except RuntimeError:
            ctx.diag_timer = None
            ctx.diag_timer_key = None

    def _emit_no_audio_diag(
        self, ctx: AdapterContext, loop: asyncio.AbstractEventLoop, key: Optional[str]
    ) -> None:
        if not config.DIAG_AUDIO_GUARD:
            return
        if ctx.diag_timer_key != key:
            return
        ctx.diag_timer = None
        tts_end_ts = ctx.tts_end_ts
        if tts_end_ts is None or ctx.diag_audio_seen:
            return

        elapsed = time.monotonic() - tts_end_ts
        if elapsed <= 8.0:
            delay = max(0.5, 8.0 - elapsed)
            try:
                ctx.diag_timer = loop.call_later(
                    delay,
                    self._emit_no_audio_diag,
                    ctx,
                    loop,
                    key,
                )
            except RuntimeError:
                ctx.diag_timer = None
            return

        bus.publish(
            {
                "type": "EVT_DIAG_NO_AUDIO_FROM_CLIENT",
                "sid": ctx.sid,
                "since_ms": int(elapsed * 1000),
                "suggestions": ["permission", "device", "recorder", "transport"],
            }
        )
        ctx.diag_audio_seen = True
        ctx.diag_timer_key = None

    @staticmethod
    def _cancel_diag_timer(ctx: AdapterContext) -> None:
        timer = ctx.diag_timer
        ctx.diag_timer = None
        ctx.diag_timer_key = None
        if timer is not None:
            timer.cancel()

    def _emit_hud_state(self, ctx: AdapterContext, state: str) -> None:
        if ctx.hud_state == state:
            return

        ctx.hud_state = state
        try:
            asyncio.create_task(self._publish(EVT_HUD_STATE, ctx.sid, {"state": state}))
        except RuntimeError:
            _log.warning("evt=ws_hud_state_publish_failed sid=%s", ctx.sid)

    def _emit_client_mic_open(
        self,
        ctx: AdapterContext,
        *,
        vendor: Optional[str] = None,
        opened_ts: Optional[int] = None,
    ) -> None:
        if ctx.client_mic_open:
            return

        self._cancel_mic_open_timer(ctx)
        ctx.client_mic_open = True
        payload: Dict[str, object] = {"state": "open"}
        if isinstance(vendor, str) and vendor:
            payload["vendor"] = vendor[:64]
        if isinstance(opened_ts, int):
            payload["ts"] = opened_ts
        _log.info(
            "evt=ws_client_mic_open sid=%s vendor=%s ts=%s",
            ctx.sid,
            payload.get("vendor") or "",
            payload.get("ts"),
        )
        try:
            asyncio.create_task(
                self._publish(EVT_CLIENT_MIC_OPEN, ctx.sid, payload)
            )
        except RuntimeError:
            _log.warning("evt=ws_client_mic_publish_failed sid=%s", ctx.sid)

    def _handle_client_audio_activity(self, ctx: AdapterContext) -> None:
        if ctx.client_mic_open:
            return

        was_nudged = ctx.mic_nudge_sent
        ctx.mic_nudge_sent = False
        self._cancel_mic_open_timer(ctx)
        if was_nudged:
            _log.info("evt=ws_mic_open_recovered sid=%s", ctx.sid)
        self._emit_client_mic_open(ctx)

    def _schedule_mic_open_guard(
        self, ctx: AdapterContext, loop: asyncio.AbstractEventLoop
    ) -> None:
        if _MIC_OPEN_TIMEOUT_SECONDS <= 0:
            return
        if ctx.client_mic_open:
            return
        self._cancel_mic_open_timer(ctx)

        def _fire() -> None:
            ctx.mic_open_timer = None
            self._handle_mic_open_timeout(ctx)

        try:
            _log.info(
                "evt=ws_mic_guard_arm sid=%s timeout_s=%.2f",
                ctx.sid,
                _MIC_OPEN_TIMEOUT_SECONDS,
            )
            ctx.mic_open_timer = loop.call_later(_MIC_OPEN_TIMEOUT_SECONDS, _fire)
        except RuntimeError:
            ctx.mic_open_timer = None

    def _handle_mic_open_timeout(self, ctx: AdapterContext) -> None:
        if ctx.client_mic_open:
            return
        if ctx.mic_nudge_sent:
            return

        ctx.mic_nudge_sent = True
        _log.warning(
            "evt=ws_mic_open_timeout sid=%s timeout_s=%.2f mic_open=%s nudged=%s",
            ctx.sid,
            _MIC_OPEN_TIMEOUT_SECONDS,
            ctx.client_mic_open,
            ctx.mic_nudge_sent,
        )
        try:
            asyncio.create_task(
                self._publish(
                    "EVT_MIC_OPEN_TIMEOUT",
                    ctx.sid,
                    {"timeout_s": _MIC_OPEN_TIMEOUT_SECONDS},
                )
            )
        except RuntimeError:
            pass
        if ctx.outbox is not None:
            nudge_frame = {
                "type": "hud.nudge",
                "code": "mic_permissions",
                "reason": "mic_open_timeout",
            }
            bus.publish(
                {
                    "type": EVT_WS_JSON_SEND,
                    "sid": ctx.sid,
                    "payload": nudge_frame,
                    "frame": nudge_frame,
                }
            )

    @staticmethod
    def _cancel_mic_open_timer(ctx: AdapterContext) -> None:
        timer = ctx.mic_open_timer
        ctx.mic_open_timer = None
        if timer is not None:
            _log.info("evt=ws_mic_guard_cancel sid=%s", ctx.sid)
            timer.cancel()

    def _initiate_listen_handoff(self, ctx: AdapterContext, req_id: str) -> bool:
        if not ctx.await_user_expected or ctx.await_user_pending:
            return False

        runtime = getattr(self, "asr_runtime", None)
        if runtime is None:
            return False

        prearm = getattr(runtime, "prearm", None)
        if not callable(prearm):
            return False

        key = self._turn_key(ctx, req_id)
        self._set_pending_for_key(ctx, key)
        self._clear_after_mask_for_key(ctx, key)
        try:
            prearm(ctx.sid)
        except Exception:  # pragma: no cover - defensive logging
            self._clear_pending_for_key(ctx, key)
            _log.exception("evt=ws_asr_prearm_failed sid=%s", ctx.sid)
            return False

        return True

    def _stop_asr_ready_tracker(self, ctx: AdapterContext) -> None:
        token = ctx.asr_subscription_token
        ctx.asr_subscription_token = None
        telemetry_bus = ctx.asr_subscription_bus or bus
        ctx.asr_subscription_bus = None
        if token:
            telemetry_bus.unsubscribe(token)
        ctx.asr_ready = False
        ctx.awaiting_asr_ready = False
        ctx.client_capture_armed = False
        ctx.asr_recovering_until = 0.0
        ctx.asr_recovering_reason = None
        ctx.asr_recovering_audio_logged = False
        ctx.await_user_after_mask = False

        open_token = ctx.asr_open_subscription_token
        ctx.asr_open_subscription_token = None
        if open_token:
            bus.unsubscribe(open_token)
        
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

    async def _publish(
        self,
        event_type: str,
        sid: str,
        meta: Dict[str, Any],
        payload: Optional[Mapping[str, Any]] = None,
    ) -> None:
        # Special handling: session-step telemetry
        if event_type == EVT_SESSION_STEP:
            event = {
                "schema_version": "1",
                "type": EVT_SESSION_STEP,   # normalize type explicitly
                "sid": sid,
                "who": "server",
                "source": "ws_server",
                "meta": dict(meta),
            }
            bus.publish(event)
            return

        event = {
            "schema_version": "1",
            "type": event_type,
            "sid": sid,
            "who": "server",
            "source": "ws_server",
            "meta": dict(meta),
        }
        if payload is not None:
            if isinstance(payload, Mapping):
                frame_payload = dict(payload)
            else:
                frame_payload = payload
            event["payload"] = frame_payload
            if isinstance(frame_payload, Mapping):
                frame_copy = dict(frame_payload)
                if event_type == EVT_WS_JSON_SEND:
                    event["frame"] = frame_copy
                else:
                    event.setdefault("frame", frame_copy)
        bus.publish(event)

    def _emit_session_step(
        self,
        sid: str,
        step: str,
        *,
        summary: Optional[str] = None,
        meta: Optional[Mapping[str, Any]] = None,
        source: str = "ws_server",
        who: str = "server",
    ) -> None:
        payload: Dict[str, Any] = {
            "schema_version": "1",
            "type": EVT_SESSION_STEP,
            "sid": sid,
            "who": who,
            "source": source,
            "meta": {"step": step},
        }
        if meta:
            try:
                payload["meta"].update(meta)
            except Exception:
                payload["meta"]["detail"] = repr(meta)
        if summary:
            payload["summary"] = summary
        bus.publish(payload)

    async def _invoke_engine(self, hook: str, *args: Any) -> None:
        if not self.engine:
            return
        handler = getattr(self.engine, hook, None)
        if handler is None:
            return
        try:
            result = handler(*args)
            if inspect.isawaitable(result):
                await result
        except Exception:
            sid = args[0] if args else None
            _log.exception("evt=ws_engine_hook_failed hook=%s sid=%s", hook, sid)
            raise

    async def _on_open_and_greet(
        self,
        ctx: AdapterContext,
    ) -> None:
        try:
            _log.info("evt=ws_open_and_greet_start sid=%s", ctx.sid)
            self._emit_session_step(
                ctx.sid,
                "ws.open_and_greet",
                summary="Invoking engine open + greet",
                source="ws.engine",
            )
            await self._invoke_engine("on_open", ctx.sid, ctx.headers)
            await self._invoke_engine("start_greet", ctx.sid)
        except Exception:  # pragma: no cover - defensive logging
            _log.exception("evt=ws_open_task_failed sid=%s", ctx.sid)

    def _policy_snapshot(self) -> Dict[str, Any]:
        engine = self.engine
        if not engine:
            return {}
        try:
            snapshot = getattr(engine, "policy_snapshot", None)
            if callable(snapshot) and not isinstance(snapshot, dict):
                snapshot = snapshot()
        except Exception:  # pragma: no cover - defensive logging
            _log.exception("evt=ws_policy_snapshot_error")
            return {}
        if not isinstance(snapshot, dict):
            return {}
        stable: Dict[str, Any] = {}
        for key in _POLICY_STABLE_KEYS:
            if key in snapshot:
                stable[key] = snapshot[key]
        media = snapshot.get("media")
        if isinstance(media, dict):
            stable["media"] = {
                key: media[key]
                for key in ("asr_input", "asr_rate_hz", "asr_channels")
                if key in media
            }
        capture = snapshot.get("capture")
        if isinstance(capture, dict):
            stable["capture"] = dict(capture)
        audio = snapshot.get("audio")
        if isinstance(audio, dict):
            stable["audio"] = dict(audio)
        nested_policy = snapshot.get("policy")
        if isinstance(nested_policy, dict):
            stable["policy"] = json.loads(json.dumps(nested_policy))
        return stable

    def _allowed_asr_vendors(self) -> List[str]:
        allowed: List[str] = []
        vendor = "speechmatics"
        speechmatics_enabled = getattr(config, "ASR_SPEECHMATICS_ENABLED", True)
        api_key = getattr(config, "SPEECHMATICS_API_KEY", None)
        url = getattr(config, "SPEECHMATICS_REALTIME_URL", None)

        if not speechmatics_enabled:
            _log.warning(
                "evt=asr_vendor_disabled sid=%s vendor=%s scope=config",
                current_sid.get(None),
                vendor,
            )
        if not api_key:
            _log.warning(
                "evt=asr_vendor_missing sid=%s vendor=%s missing=api_key",
                current_sid.get(None),
                vendor,
            )
        if not url:
            _log.warning(
                "evt=asr_vendor_missing sid=%s vendor=%s missing=url",
                current_sid.get(None),
                vendor,
            )

        allowed.append(vendor)
        return allowed

    @staticmethod
    def _extract_policy_vendor(snapshot: Mapping[str, Any] | None) -> tuple[Optional[str], Optional[str]]:
        if not isinstance(snapshot, Mapping):
            return None, None
        policy_block = snapshot.get("policy")
        if not isinstance(policy_block, Mapping):
            return None, None
        asr_block = policy_block.get("asr")
        if not isinstance(asr_block, Mapping):
            return None, None
        vendor_block = asr_block.get("vendor")
        if not isinstance(vendor_block, Mapping):
            return None, None
        primary = vendor_block.get("primary")
        secondary = vendor_block.get("secondary")
        normalized_primary = (
            primary.strip().lower()
            if isinstance(primary, str) and primary.strip()
            else None
        )
        normalized_secondary: Optional[str]
        if secondary is None:
            normalized_secondary = None
        elif isinstance(secondary, str) and secondary.strip():
            normalized_secondary = secondary.strip().lower()
        else:
            normalized_secondary = None
        return normalized_primary, normalized_secondary

    def _select_asr_vendor(
        self,
        ctx: AdapterContext,
        snapshot: Mapping[str, Any] | None,
        allowed: List[str],
        *,
        log_selection: bool = True,
    ) -> tuple[Optional[str], str]:
        primary_policy, secondary_policy = self._extract_policy_vendor(snapshot)
        reason = "fallback"
        selected: Optional[str] = None

        if primary_policy:
            if primary_policy in allowed:
                selected = primary_policy
                reason = "policy"
            else:
                _log.warning(
                    "evt=policy_violation sid=%s vendor=%s scope=asr.vendor.primary",
                    ctx.sid,
                    primary_policy,
                )
        if selected is None and secondary_policy:
            if secondary_policy in allowed:
                selected = secondary_policy
                reason = "policy"
            else:
                _log.warning(
                    "evt=policy_violation sid=%s vendor=%s scope=asr.vendor.secondary",
                    ctx.sid,
                    secondary_policy,
                )
        if selected is None:
            selected = "speechmatics"
            reason = "forced"

        if log_selection:
            allowed_display = ",".join(allowed)
            _log.info(
                "asr_vendor_selected primary=%s allowed=%s reason=%s",
                selected or "",
                allowed_display,
                reason,
            )
            ctx.asr_vendor_logged = True
        return selected, reason

    @staticmethod
    def _resolve_audio_pipeline_mode(snapshot: Mapping[str, Any] | None) -> str:
        if isinstance(snapshot, Mapping):
            audio_block = snapshot.get("audio")
            if isinstance(audio_block, Mapping):
                pipeline = audio_block.get("pipeline")
                if isinstance(pipeline, Mapping):
                    mode = pipeline.get("mode")
                    if isinstance(mode, str) and mode.strip():
                        normalized = mode.strip().lower()
                        if normalized == "pcm16":
                            return normalized
        return "pcm16"

    @staticmethod
    def _input_descriptor_for_mode(mode: str) -> Dict[str, Any]:
        if mode == "pcm16":
            return {
                "mode": "pcm16",
                "container": "raw",
                "codec": "pcm_s16le",
                "rate_hz": 16000,
                "channels": 1,
                "mime": "audio/raw;rate=16000;channels=1;format=s16le",
            }
        return {
            "mode": "pcm16",
            "container": "raw",
            "codec": "pcm_s16le",
            "rate_hz": 16000,
            "channels": 1,
            "mime": "audio/raw;rate=16000;channels=1;format=s16le",
        }

    @staticmethod
    def _session_capture_policy_for_mode(mode: str) -> Dict[str, Any]:
        if mode == "pcm16":
            return {
                "media": {
                    "asr_input": "pcm_16k",
                    "asr_rate_hz": 16000,
                    "asr_channels": 1,
                },
                "capture": {
                    "asr_input": "pcm_16k",
                    "sample_rate": 16000,
                    "channels": 1,
                    "timeslice_ms": 50,
                },
                "audio": {"pipeline": {"mode": "pcm16"}},
            }
        return {
            "media": {
                "asr_input": "pcm_16k",
                "asr_rate_hz": 16000,
                "asr_channels": 1,
            },
            "capture": {
                "asr_input": "pcm_16k",
                "sample_rate": 16000,
                "channels": 1,
                "timeslice_ms": 50,
            },
            "audio": {"pipeline": {"mode": "pcm16"}},
        }

    def _resolve_capture_timeslice(self, ctx: AdapterContext, mode: str) -> int:
        candidates: List[Mapping[str, Any]] = []
        snapshot = ctx.policy_snapshot if isinstance(ctx.policy_snapshot, Mapping) else None
        if isinstance(snapshot, Mapping):
            capture_block = snapshot.get("capture")
            if isinstance(capture_block, Mapping):
                candidates.append(capture_block)
        session_policy = ctx.session_capture_policy
        if isinstance(session_policy, Mapping):
            capture_policy = session_policy.get("capture")
            if isinstance(capture_policy, Mapping):
                candidates.append(capture_policy)

        for candidate in candidates:
            raw_value = candidate.get("timeslice_ms")
            try:
                parsed = int(raw_value)
            except (TypeError, ValueError):
                continue
            if parsed > 0:
                return parsed

        return 50 if mode == "pcm16" else 250

    def _log_policy_flags(self, sid: str, snapshot: Mapping[str, Any]) -> None:
        if not isinstance(snapshot, Mapping):
            return
        policy_block = snapshot.get("policy")
        if not isinstance(policy_block, Mapping):
            return

        recorder_block = policy_block.get("recorder")
        input_block = policy_block.get("input")
        asr_block = policy_block.get("asr")
        routing_block = policy_block.get("routing")

        stop_on_tts = bool(
            recorder_block.get("stop_on_tts_start") if isinstance(recorder_block, Mapping) else False
        )
        mute_during_tts = bool(
            recorder_block.get("mute_send_during_tts") if isinstance(recorder_block, Mapping) else False
        )
        require_hotword = bool(
            input_block.get("require_hotword_to_start") if isinstance(input_block, Mapping) else False
        )
        prearm_on_tts_end = bool(
            asr_block.get("prearm_on_tts_end") if isinstance(asr_block, Mapping) else False
        )
        keep_warm_raw = (
            asr_block.get("keep_stream_warm_ms") if isinstance(asr_block, Mapping) else 0
        )
        try:
            keep_warm_ms = int(keep_warm_raw)
        except (TypeError, ValueError):
            keep_warm_ms = 0

        commit_on_vad = bool(
            asr_block.get("commit_on_vad_silence") if isinstance(asr_block, Mapping) else False
        )
        commit_silence_raw = (
            asr_block.get("commit_silence_ms") if isinstance(asr_block, Mapping) else 0
        )
        try:
            commit_silence_ms = int(commit_silence_raw)
        except (TypeError, ValueError):
            commit_silence_ms = 0
        max_utterance_raw = (
            asr_block.get("max_utterance_ms") if isinstance(asr_block, Mapping) else 0
        )
        try:
            max_utterance_ms = int(max_utterance_raw)
        except (TypeError, ValueError):
            max_utterance_ms = 0

        vendor_primary = ""
        vendor_secondary = ""
        if isinstance(asr_block, Mapping):
            vendor_block = asr_block.get("vendor")
            if isinstance(vendor_block, Mapping):
                primary_val = vendor_block.get("primary")
                secondary_val = vendor_block.get("secondary")
                if isinstance(primary_val, str):
                    vendor_primary = primary_val.strip().lower()
                if isinstance(secondary_val, str):
                    vendor_secondary = secondary_val.strip().lower()
                elif secondary_val is None:
                    vendor_secondary = ""

        audio_mode = self._resolve_audio_pipeline_mode(snapshot)

        routing_dict = dict(routing_block) if isinstance(routing_block, Mapping) else {}
        ws_version = str(routing_dict.get("ws_version") or "").strip() or "v2"
        if ws_version != "v2":
            _log.warning(
                "evt=policy_routing_normalized sid=%s configured_ws_version=%s normalized_ws_version=v2",
                sid,
                ws_version,
            )
            ws_version = "v2"
        routing_dict["ws_version"] = ws_version
        policy_block_mut = dict(policy_block)
        policy_block_mut["routing"] = routing_dict
        snapshot["policy"] = policy_block_mut

        _log.info(
            "evt=policy_snapshot sid=%s recorder.stop_on_tts_start=%s recorder.mute_send_during_tts=%s "
            "input.require_hotword_to_start=%s asr.prearm_on_tts_end=%s asr.keep_stream_warm_ms=%s "
            "asr.commit_on_vad_silence=%s asr.commit_silence_ms=%s asr.max_utterance_ms=%s "
            "asr.vendor.primary=%s asr.vendor.secondary=%s audio.pipeline.mode=%s ws_version=%s",
            sid,
            stop_on_tts,
            mute_during_tts,
            require_hotword,
            prearm_on_tts_end,
            keep_warm_ms,
            commit_on_vad,
            commit_silence_ms,
            max_utterance_ms,
            vendor_primary or "",
            vendor_secondary or "",
            audio_mode,
            ws_version,
        )

    def _policy_keep_warm_ms(self, ctx: AdapterContext) -> int:
        snapshot = ctx.policy_snapshot if isinstance(ctx.policy_snapshot, Mapping) else {}
        policy_block = snapshot.get("policy") if isinstance(snapshot, Mapping) else None
        if not isinstance(policy_block, Mapping):
            return 0
        asr_block = policy_block.get("asr")
        if not isinstance(asr_block, Mapping):
            return 0
        value = asr_block.get("keep_stream_warm_ms")
        try:
            keep_warm = int(value)
        except (TypeError, ValueError):
            keep_warm = 0
        if keep_warm < 0:
            return 0
        return keep_warm

    async def _send_asr_ready_bundle(
        self,
        send: Callable[[dict], Awaitable[None]],
        ctx: AdapterContext,
    ) -> None:
        mode = ctx.audio_pipeline_mode or "pcm16"
        descriptor = dict(self._input_descriptor_for_mode(mode))
        timeslice_ms = self._resolve_capture_timeslice(ctx, mode)
        vendor = ctx.asr_vendor or "speechmatics"
        ready_frame = {
            "type": "asr.ready",
            "input": dict(descriptor),
            "vendor": vendor,
        }
        capture = dict(descriptor)
        capture["timeslice_ms"] = timeslice_ms
        capture["manual_gate"] = False
        session_policy = ctx.session_capture_policy or self._session_capture_policy_for_mode(mode)
        input_start = {
            "type": "input.start",
            "capture": capture,
        }
        if session_policy:
            input_start["policy"] = session_policy

        start_payload: Dict[str, Any] = {"type": "start_listening"}
        if session_policy:
            start_payload["policy"] = session_policy

        now_ms = int(time.time() * 1000)
        ctx.awaiting_asr_ready = True
        ctx.client_capture_armed = True
        ctx.asr_recovering_until = 0.0
        ctx.asr_recovering_reason = None
        ctx.asr_recovering_audio_logged = False
        ctx.ingress_packets = 0
        ctx.ingress_bytes = 0
        ctx.first_ingress_ms = None
        ctx.mic_armed_ms = now_ms
        ctx.asr_ready_bundle_sent_ms = now_ms

        await self._send_json(send, ctx.sid, ready_frame)
        await self._send_json(send, ctx.sid, input_start)
        await self._send_json(send, ctx.sid, start_payload)
        _log.info(
            "evt=asr_ready_bundle_sent sid=%s input.mode=%s input.mime=%s capture.timeslice_ms=%s vendor=%s",
            ctx.sid,
            mode,
            descriptor.get("mime", ""),
            timeslice_ms,
            vendor,
        )

    @staticmethod
    def _decode_headers(headers: Iterable[tuple[bytes, bytes]]) -> Dict[str, str]:
        decoded: Dict[str, str] = {}
        for key, value in headers:
            decoded[key.decode("latin1").lower()] = value.decode("latin1")
        return decoded

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

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

    @staticmethod
    def _ensure_send_lock(ctx: AdapterContext) -> asyncio.Lock:
        lock = ctx.send_lock
        if lock is None:
            lock = asyncio.Lock()
            ctx.send_lock = lock
        return lock


__all__ = [
    "ChatV2Adapter",
    "CHAT_V2_SUBPROTOCOL",
    "QUEUE_ON_THRESHOLD",
    "QUEUE_OFF_THRESHOLD",
    "EVT_BACKPRESSURE_ON",
    "EVT_BACKPRESSURE_OFF",
    "EVT_WS_OUTBOX_DROP",
]
