# // CLEAN BUILD (2025-11-06): PCM16@16k mono ONLY; no MediaRecorder/WebM/Opus/Deepgram.
"""chat.v2 WebSocket adapter for AskChip."""
from __future__ import annotations

import os
import array
import asyncio
import contextlib
import logging
import math
import threading
import time
import inspect
import platform
import socket
import sys
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Iterable,
    List,
    Literal,
    Mapping,
    Optional,
    Deque,
    Protocol,
    runtime_checkable,
)

import json
from urllib.parse import parse_qs

try:
    import msgpack  # type: ignore[import-untyped]
except Exception:  # pragma: no cover - optional dependency fallback
    msgpack = None  # type: ignore[assignment]

from app import config
from app.config_build import current_build_id
from app.logging_setup import current_sid
from app.security.jwt_utils import verify_ws_token
from app.telemetry import bus
from app.telemetry.events import (
    AUDIO_WIRE_ROLLUP,
    ASR_KEEPALIVE_PING,
    ASR_OPEN_AFTER_TTS,
    ASR_OPEN_DEDUP,
    ASR_OPEN_QUEUED,
    ASR_POST_CLOSE_DROP,
    ASR_SINGLE_STREAM_INVARIANT,
    ASR_VENDOR_BYTES_TOTAL,
    ASR_ROLLUP,
    CLIENT_IDLE_TICK,
    EVT_ASR_CLOSE,
    EVT_ASR_OPEN,
    EVT_SESSION_TRANSPORT_CLOSED,
    SESSION_TEARDOWN_COMPLETE,
    WS_AUDIO_FIRST_CHUNK,
    WS_AUDIO_HEADER_ACCEPT,
)
from app.telemetry.exporter import FileExporter
from app.voice_v2 import (
    EVT_ASR_CLOSED,
    EVT_ASR_FINAL,
    EVT_ASR_PARTIAL,
    EVT_ASR_READY,
    EVT_CHAT_USER,
    EVT_CLIENT_BANNER,
    EVT_CLIENT_AUTOSTART,
    EVT_CLIENT_MIC_OPEN,
    EVT_HUD_STATE,
    EVT_SESSION_STEP,
    EVT_TTS_START,
    EVT_TTS_END,
    EVT_TTS_MASK,
    EVT_CLIENT_LOG,
    EVT_WS_AUDIO_RECV,
    EVT_WS_AUDIO_SEND,
    EVT_WS_JSON_RECV,
    EVT_WS_JSON_SEND,
)
from app.services.asr.gcp_engine import ASREngine, GCPStreamingASREngine
from app.ws.validator import validate_audio_header_against_policy, validate_frame
from app.ws.state import SessionCtx, can_open, mark
from app.ws.policy import normalize_policy

try:  # pragma: no cover - uvicorn is an optional dependency in tests
    from uvicorn.protocols.utils import ClientDisconnected
except Exception:  # pragma: no cover - fallback when uvicorn missing
    class ClientDisconnected(Exception):  # type: ignore[no-redef]
        """Fallback placeholder when uvicorn is unavailable."""


CHAT_V2_SUBPROTOCOL = "chat.v2"
CHAT_MSGPACK_SUBPROTOCOL = "chip-msgpack"
TEXT_FRAME_LIMIT_BYTES = 64 * 1024
BINARY_FRAME_LIMIT_BYTES = 2 * 1024 * 1024
FEATURE_LEGACY_POLICY = os.getenv("FEATURE_LEGACY_POLICY", "false").lower() == "true"
ALLOW_AUDIO_WITHOUT_ASR = os.getenv("ALLOW_AUDIO_WITHOUT_ASR", "0") == "1"
PING_MIN_INTERVAL_MS = 500
RATE_LIMIT_CAPACITY = 25
RATE_LIMIT_WINDOW_SECONDS = 2.0
RATE_LIMIT_CLOSE_CODE = 1013
_AUDIO_VIOLATION_LIMIT = 3
PCM_BYTES_PER_SAMPLE = 2
AUDIO_KEEPALIVE_CHUNK_MS = 20

AUDIO_SEQ_WINDOW = 8

QUEUE_ON_THRESHOLD = 12
QUEUE_OFF_THRESHOLD = 6

_AUDIO_THROTTLE_HINT_MS = 250
_AUDIO_THROTTLE_COOLDOWN_MS = 600
_AUDIO_THROTTLE_QUEUE_THRESHOLD = QUEUE_ON_THRESHOLD
_AUDIO_THROTTLE_AUDIO_TASK_THRESHOLD = 4

_DEFAULT_GCP_SAMPLE_RATE_HZ = 16000

_DEFAULT_WS_PING_INTERVAL_MS = 10_000
_HEARTBEAT_TIMEOUT_MS = 30_000

_PERMESSAGE_DEFLATE_HEADER = (
    b"permessage-deflate; client_no_context_takeover; server_no_context_takeover"
)

EVT_BACKPRESSURE_ON = "EVT_BACKPRESSURE_ON"
EVT_BACKPRESSURE_OFF = "EVT_BACKPRESSURE_OFF"

EVT_RATE_LIMIT = "EVT_RATE_LIMIT"

EVT_WS_OUTBOX_DROP = "EVT_WS_OUTBOX_DROP"

EVT_WS_JSON_SEND_SUMMARY = "ws.json.send_summary"
EVT_WS_JSON_RECV_SUMMARY = "ws.json.recv_summary"
ASR_CLOSE_DEDUP = "ASR_CLOSE_DEDUP"

_DIAG_NO_AUDIO_CHECK_DELAY_SECONDS = 8.5
_MIC_OPEN_TIMEOUT_SECONDS = 2.5

_OUTBOUND_ALLOWED_TYPES = {
    "policy.interaction",
    "info",
    "tts.start",
    "tts.end",
    "asr.ready",
    "input.start",
    "input.stop",
    "asr.input.start",
    "asr.input.stop",
    "asr.partial",
    "asr.final",
    "asr.unavailable",
    "error",
    "chat.message",
    "chat.begin",
    "chat.delta",
    "chat.commit",
    "chat.end",
    "chat.history",
    "dialog.plan",
    "hud.nudge",
    "turn.begin",
    "turn.end",
}

_RATE_LIMIT_EXEMPT_TYPES = {
    "client.autostart",
    "client.banner",
    "client.diag",
    "client.idle",
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
    "client.idle",
    "client.log",
    "client.autostart",
    "client.telemetry",
    "client.banner",
    "asr.rearm.request",
    "asr.open",
    "asr.close",
}

_CLIENT_TELEMETRY_ALLOWED_EVENTS = {
    "EVT_AUDIO_CHUNK_SENT_CLIENT",
    "client.vad.speech_start",
    "client.vad.speech_end",
    "client.vad.state",
    "client.vad.gate",
    "client.vad.gate_heartbeat",
    "client.appstate.delta",
    "client.appstate.heartbeat",
}

_SERVER_VAD_DEFAULT_POLICY = {
    "enable": True,
    "min_speech_ms": 200,
    "min_silence_ms": 700,
    "eot_silence_ms": 800,
    "fuse_mode": "and",
    "weight_client": 0.5,
    "weight_vendor": 0.5,
    "energy_threshold_dbfs": -45.0,
}

_SERVER_VAD_EVAL_INTERVAL_MS = 100
_PCM_INT16_MAX = 32768.0
_DB_FLOOR = -120.0


def _pcm16_rms_dbfs(payload: bytes) -> float:
    if not payload:
        return _DB_FLOOR
    view = memoryview(payload)
    sample_count = len(view) // 2
    if sample_count <= 0:
        return _DB_FLOOR
    samples = array.array("h")
    samples.frombytes(view[: sample_count * 2])
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return _DB_FLOOR
    acc = 0.0
    for sample in samples:
        normalized = sample / _PCM_INT16_MAX
        acc += normalized * normalized
    mean_square = acc / len(samples)
    if mean_square <= 0.0:
        return _DB_FLOOR
    rms = math.sqrt(mean_square)
    if rms <= 0.0:
        return _DB_FLOOR
    return 20.0 * math.log10(rms)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))

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
    session: SessionCtx = field(init=False)
    user_id: Optional[str] = None
    is_admin: bool = False
    principal: Dict[str, Any] = field(default_factory=dict)
    control_codec: Literal["json", "msgpack"] = "json"
    audio_seq: int = 0
    audio_expected_seq: int = 0
    audio_highest_seq: int = -1
    audio_buffer: Dict[int, bytes] = field(default_factory=dict)
    audio_backlog: Deque[tuple[int, bytes]] = field(default_factory=deque)
    audio_backlog_bytes: int = 0
    audio_window: int = AUDIO_SEQ_WINDOW
    audio_chunks_recv: int = 0
    audio_bytes_recv: int = 0
    ingress_packets: int = 0
    ingress_bytes: int = 0
    first_ingress_ms: Optional[int] = None
    ing_frames: int = 0
    ing_bytes: int = 0
    ing_chunks: int = 0
    ing_last_tick_t0_ms: Optional[int] = None
    ing_tick_task: asyncio.TimerHandle | None = None
    no_audio_timer: asyncio.TimerHandle | None = None
    no_audio_watchdog_t0_ms: Optional[int] = None
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
    client_turn_closed: bool = False
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
    asr_closed_subscription_token: Optional[str] = None
    asr_open: bool = False
    asr_recovering_until: float = 0.0
    asr_recovering_reason: Optional[str] = None
    asr_recovering_audio_logged: bool = False
    server_keepalive_task: asyncio.Task[None] | None = None
    last_policy_interaction: Optional[Dict[str, Any]] = None
    policy_snapshot: Optional[Dict[str, Any]] = None
    policy: Optional[Dict[str, Any]] = None
    policy_warning_logged: bool = False
    policy_snapshot_fingerprint: Optional[str] = None
    policy_snapshot_logged: bool = False
    asr_first_packet_logged: bool = False
    asr_silence_hold_logged: bool = False
    asr_silence_eot_logged: bool = False
    asr_first_packet_monotonic: Optional[float] = None
    partial_seq: int = 0
    partial_coalescer: _PartialCoalescer = field(default_factory=_PartialCoalescer)
    last_asr_partial: Optional[str] = None
    send_lock: asyncio.Lock | None = None
    ws_send: Callable[[dict], Awaitable[None]] | None = None
    asr_open_task: asyncio.Task[None] | None = None
    asr_bytes_sent: int = 0
    asr_opened_ms: Optional[int] = None
    asr_close_reason: Optional[str] = None
    asr_final_emitted: bool = False
    asr_closed_ack_sent: bool = False
    asr_stream_id: Optional[str] = None
    asr_stream_req_id: Optional[str] = None
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
    pending_start_listening: Optional[Dict[str, Any]] = None
    pending_start_listening_sent: bool = False
    tts_end_subscription_token: Optional[str] = None
    tts_bus_token_start: Optional[str] = None
    tts_bus_token_end: Optional[str] = None
    turn_state_subscription_token: Optional[str] = None
    hud_state: Optional[str] = None
    client_mic_open: bool = False
    turn_active: bool = False
    mic_open_timer: asyncio.TimerHandle | None = None
    mic_nudge_sent: bool = False
    await_user_req_id: Optional[str] = None
    last_tts_end_req_id: Optional[str] = None
    await_user_cue_emitted: bool = False
    await_user_vad_check_pending: bool = False
    listen_handoff_done: set[str] = field(default_factory=set)
    listen_handoff_task: asyncio.Task[None] | None = None
    listen_handoff_task_key: Optional[str] = None
    asr_ready_deadline_task: asyncio.Task[None] | None = None
    listen_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    client_banner_info: Optional[Dict[str, Any]] = None
    client_banner_events: List[Dict[str, Any]] = field(default_factory=list)
    allowed_asr_vendors: List[str] = field(default_factory=list)
    asr_vendor: Optional[str] = None
    audio_pipeline_mode: Optional[str] = None
    asr_vendor_logged: bool = False
    session_capture_policy: Optional[Dict[str, Any]] = None
    client_vad_speech: bool = False
    client_vad_since_ms: Optional[int] = None
    client_vad_last_event_ms: Optional[int] = None
    client_vad_last_speech_end_ms: Optional[int] = None
    client_vad_confidence: float = 0.0
    client_vad_energy_db: Optional[float] = None
    client_vad_noise_db: Optional[float] = None
    server_vad_candidate_start_ms: Optional[int] = None
    server_vad_silence_candidate_ms: Optional[int] = None
    server_vad_energy_db: Optional[float] = None
    vad_fusion_task: asyncio.Task[None] | None = None
    input_start_ms: Optional[int] = None
    first_partial_logged: bool = False
    first_final_logged: bool = False
    active_asr_config: Optional[Dict[str, Any]] = None
    hub_log_last_turn: Optional[str] = None
    no_audio_rearm_handle: asyncio.TimerHandle | None = None
    hub_log_seq: int = 0
    asr_turn_active: bool = False
    asr_turn_begin_sent: bool = False
    asr_turn_armed_sent: bool = False

    def __post_init__(self) -> None:
        self.session = SessionCtx(sid=self.sid, policy=None)


class ChatV2Adapter:
    """Minimal chat.v2 WebSocket adapter with telemetry taps."""

    THROTTLE_COOLDOWN_MS = 800
    THROTTLE_GRACE_AFTER_READY_MS = 2000
    THROTTLE_BURST_MS = 250
    THROTTLE_BACKLOG_FRAMES = 6
    THROTTLE_BACKLOG_MS = 600
    THROTTLE_RING_BUFFER_MAX_BYTES = 16384

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
        self._policy_defaults_emitted: Dict[Optional[str], bool] = {}
        self._policy_env_warning_logged = False
        self._last_throttle_emit_ms: int = 0
        # (no-op if unused; helps explicitness)
        self._noop = None

    async def _call_openai(self, ctx: AdapterContext, transcript: str) -> str:
        """Execute an OpenAI Chat Completions request without blocking the event loop."""
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            _log.error("evt=llm_error reason=api_key_missing sid=%s", ctx.sid)
            return "I'm sorry, my language model key is not configured."

        try:
            from openai import OpenAI  # Local import to avoid hard dependency at import time.
        except ImportError:
            return "OpenAI SDK not correctly installed on the server."

        try:
            client = OpenAI(api_key=api_key)
            response = await asyncio.to_thread(
                client.chat.completions.create,
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a friendly and concise AI assistant. Respond to the "
                            "following user message concisely."
                        ),
                    },
                    {"role": "user", "content": transcript},
                ],
                temperature=0.7,
                max_tokens=150,
            )
            choice = response.choices[0]
            message = getattr(choice, "message", None)
            content = getattr(message, "content", None)
            if isinstance(content, str):
                return content.strip()
            return ""
        except NotImplementedError:
            return "OpenAI SDK not correctly installed on the server."
        except Exception as exc:  # pragma: no cover - defensive logging
            _log.error("evt=llm_openai_call_failed sid=%s error=%s", ctx.sid, str(exc))
            _log.exception("evt=llm_openai_call_failed_trace sid=%s", ctx.sid)
            return "I encountered an error while processing your request."

    def _server_policy(self, ctx: AdapterContext) -> Mapping[str, Any]:
        pol = getattr(ctx.session, "policy", None) or {}
        if not isinstance(pol, Mapping):
            return {}
        sp = pol.get("server", {}) if isinstance(pol.get("server"), Mapping) else {}
        return sp

    async def _ensure_asr_ready(
        self,
        send: Callable[[dict], Awaitable[None]] | None,
        ctx: AdapterContext,
        label: str,
    ) -> None:
        # Canonical ASR readiness path: this function decides whether to schedule
        # `_open_asr`, waits for the open task to complete, and emits the
        # `asr.ready` + `input.start` + `start_listening` bundle via
        # `_send_asr_ready_bundle`. Callers should rely on this method rather
        # than invoking `_open_asr` or `_send_asr_ready_bundle` directly.
        if ctx.asr_ready_bundle_sent_ms:
            return
        if send is None:
            return
        if getattr(ctx.session, "tts_active", False) and not self._allow_capture_during_tts(ctx):
            ctx.session.queued_arm = True
            self._log_event(
                "info", "asr_ready_deferred", ctx.sid, where=label, reason="tts_active"
            )
            return
        try:
            # Ensure an open task exists
            if ctx.asr_open_task is None or ctx.asr_open_task.done():
                self._schedule_asr_open(ctx)

            # Wait for the open task to complete before sending asr.ready
            task = ctx.asr_open_task
            if task is not None and not task.done():
                try:
                    await task
                except asyncio.CancelledError:
                    # If it was cancelled (e.g., due to TTS), just bail; TTS gating will re-arm later.
                    self._log_event(
                        "info",
                        "asr_open_task_cancelled_before_ready",
                        ctx.sid,
                        where=label,
                    )
                    return

            if not ctx.asr_ready_bundle_sent_ms:
                await self._send_asr_ready_bundle(send, ctx)
                self._log_event("info", "asr_ready_emit", ctx.sid, where=label)
        except Exception:
            self._log_event("exception", "asr_ready_emit_failed", ctx.sid, where=label)

    async def _handle_tts_start(
        self,
        send: Callable[[dict], Awaitable[None]] | None,
        ctx: AdapterContext,
        frame: Mapping[str, Any] | None,
    ) -> None:
        ctx.session.tts_active = True
        self._cancel_no_audio_watchdog(ctx)
        try:
            self._bus(
                "tts.start",
                {"sid": ctx.sid, "utt_id": frame.get("utt_id") if isinstance(frame, Mapping) else None},
            )
        except Exception:
            _log.exception("evt=tts_start_bus_failed sid=%s", ctx.sid)

    async def _handle_tts_end(
        self,
        send: Callable[[dict], Awaitable[None]] | None,
        ctx: AdapterContext,
        frame: Mapping[str, Any] | None,
    ) -> None:
        ctx.session.tts_active = False
        # cancel spurious asr_ready deadline if armed
        if ctx.asr_ready_deadline_task:
            ctx.asr_ready_deadline_task.cancel()
            ctx.asr_ready_deadline_task = None
        try:
            self._bus(
                "tts.end",
                {"sid": ctx.sid, "utt_id": frame.get("utt_id") if isinstance(frame, Mapping) else None},
            )
        except Exception:
            _log.exception("evt=tts_end_bus_failed sid=%s", ctx.sid)
        if not ctx.asr_ready_bundle_sent_ms:
            await self._ensure_asr_ready(send, ctx, "tts_end")

        self._schedule_no_audio_watchdog_rearm(ctx)

        if send is not None and not ctx.turn_active:
            try:
                await self._send_json(send, ctx.sid, {"type": "turn.begin"})
            except RuntimeError:
                _log.warning(
                    "evt=turn_begin_send_failed sid=%s reason=asgi_closed",
                    ctx.sid,
                    exc_info=True,
                )
            except Exception:  # pragma: no cover - defensive logging
                _log.warning("evt=turn_begin_send_failed sid=%s", ctx.sid, exc_info=True)
            else:
                ctx.turn_active = True

    async def _arm_asr_ready_deadline(
        self,
        send: Callable[[dict], Awaitable[None]] | None,
        ctx: AdapterContext,
        deadline_ms: int,
    ) -> None:
        try:
            await asyncio.sleep(max(0, int(deadline_ms)) / 1000.0)
            if ctx.asr_ready_bundle_sent_ms:
                return
            _log.warning("evt=asr_ready_deadline sid=%s action=force_emit", ctx.sid)
            await self._ensure_asr_ready(send, ctx, "deadline")
        finally:
            ctx.asr_ready_deadline_task = None

    @staticmethod
    def _turn_key(ctx: AdapterContext, req_id: Optional[str]) -> Optional[str]:
        if isinstance(req_id, str) and req_id:
            return f"{ctx.sid}:{req_id}"
        return None

    def _session_policy_env(self) -> Mapping[str, Any] | None:
        candidates: List[tuple[str, str]] = []
        for name in ("SESSION_POLICY", "SESSION_POLICY_JSON", "CHAT_V2_POLICY"):
            raw_value = config.get_env(name, None)
            if not isinstance(raw_value, str):
                continue
            value = raw_value.strip()
            if not value:
                continue
            candidates.append((name, value))

        for source, raw in candidates:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                if not self._policy_env_warning_logged:
                    _log.warning(
                        "evt=session_policy_env_invalid source=%s", source
                    )
                    self._policy_env_warning_logged = True
                continue
            if isinstance(parsed, Mapping):
                return parsed
            if not self._policy_env_warning_logged:
                _log.warning(
                    "evt=session_policy_env_not_mapping source=%s type=%s",
                    source,
                    type(parsed).__name__,
                )
                self._policy_env_warning_logged = True
        return None

    def _build_session_policy(
        self,
        *,
        legacy_hits: List[tuple[tuple[str, ...], tuple[str, ...]]] | None = None,
    ) -> Dict[str, Any]:
        admin_overrides = getattr(config, "POLICY_OVERRIDES", None)
        env_overrides = self._session_policy_env()
        try:
            policy = config.build_session_policy(
                admin_overrides,
                env_overrides,
                legacy_hits=legacy_hits,
            )
        except Exception:
            _log.exception("evt=session_policy_build_failed")
            policy = normalize_policy({}, legacy_hits=legacy_hits)
        return dict(policy)

    @staticmethod
    def _policy_for_client(policy: Mapping[str, Any] | None) -> Dict[str, Any]:
        if not isinstance(policy, Mapping):
            return {}
        allowed_keys = {
            "version",
            "asr",
            "vad",
            "watchdog",
            "server",
            "capture",
            "ui",
            "_normalized_from",
        }
        filtered = {key: policy[key] for key in allowed_keys if key in policy}
        try:
            return json.loads(json.dumps(filtered, ensure_ascii=False))
        except (TypeError, ValueError):
            return dict(filtered)

    def _bus(
        self, event_type: str, payload: Mapping[str, Any], *, sid: Optional[str] = None
    ) -> None:
        event_payload: Dict[str, Any]
        try:
            event_payload = json.loads(json.dumps(payload, ensure_ascii=False))
        except (TypeError, ValueError):
            event_payload = dict(payload)

        event: Dict[str, Any] = {
            "schema_version": "1",
            "type": event_type,
            "who": "server",
            "source": "ws_server",
            "payload": event_payload,
        }
        event_sid = sid if isinstance(sid, str) and sid else current_sid.get(None)
        if isinstance(event_sid, str) and event_sid:
            event["sid"] = event_sid
        bus.publish(event)

    def _emit_policy_snapshot(
        self,
        ctx: AdapterContext,
        legacy_hits: Iterable[tuple[tuple[str, ...], tuple[str, ...]]] | None = None,
    ) -> None:
        policy = ctx.policy if isinstance(ctx.policy, Mapping) else None
        if not policy:
            return
        version = policy.get("version")
        if version != 2 and not ctx.policy_warning_logged:
            _log.warning(
                "warn=policy_legacy_only; using_v2_defaults sid=%s", ctx.sid
            )
            ctx.policy_warning_logged = True
        try:
            fingerprint: Optional[str]
            try:
                fingerprint = json.dumps(policy, sort_keys=True, ensure_ascii=False)
            except (TypeError, ValueError):
                fingerprint = repr(policy)
            if ctx.policy_snapshot_fingerprint == fingerprint:
                return

            mapped_hits: List[List[List[str]]] = []
            for hit in legacy_hits or ():
                if not isinstance(hit, (list, tuple)) or len(hit) != 2:
                    continue
                legacy_path, new_path = hit
                if not isinstance(legacy_path, (list, tuple)) or not isinstance(
                    new_path, (list, tuple)
                ):
                    continue
                mapped_hits.append(
                    [list(map(str, legacy_path)), list(map(str, new_path))]
                )

            asr_candidate = policy.get("asr")
            asr_block = asr_candidate if isinstance(asr_candidate, Mapping) else {}
            vad_candidate = policy.get("vad")
            vad_block = vad_candidate if isinstance(vad_candidate, Mapping) else {}
            ui_block_candidate = policy.get("ui")
            ui_block = ui_block_candidate if isinstance(ui_block_candidate, Mapping) else {}
            ui_status_candidate = ui_block.get("status")
            ui_status_block = (
                ui_status_candidate if isinstance(ui_status_candidate, Mapping) else {}
            )
            capture_candidate = policy.get("capture")
            capture_block = capture_candidate if isinstance(capture_candidate, Mapping) else {}
            capture_mode_value = capture_block.get("mode")
            capture_constraints_candidate = capture_block.get("constraints")
            capture_constraints_block = (
                capture_constraints_candidate
                if isinstance(capture_constraints_candidate, Mapping)
                else {}
            )

            server_starts_input = None
            cold_start_grace_ms = None
            warmup_ms = None
            if isinstance(asr_block, Mapping):
                server_starts_input = asr_block.get("server_starts_input")
                cold_start_grace_ms = asr_block.get("cold_start_grace_ms")
            if isinstance(vad_block, Mapping):
                warmup_ms = vad_block.get("warmup_ms")

            snapshot_payload = {
                "version": version,
                "asr.server_starts_input": bool(asr_block.get("server_starts_input")),
                "asr.cold_start_grace_ms": self._coerce_non_negative_int(
                    asr_block.get("cold_start_grace_ms"), 0
                ),
                "vad.warmup_ms": self._coerce_non_negative_int(
                    vad_block.get("warmup_ms"), 0
                ),
                "ui.require_active_turn": bool(
                    ui_status_block.get("require_active_turn", True)
                ),
                "capture.mode": (
                    str(capture_mode_value).strip()
                    if isinstance(capture_mode_value, str)
                    else "webrtc_aec"
                ),
                "capture.constraints": {
                    "echoCancellation": capture_constraints_block.get("echoCancellation"),
                    "noiseSuppression": capture_constraints_block.get("noiseSuppression"),
                    "autoGainControl": capture_constraints_block.get("autoGainControl"),
                    "channelCount": capture_constraints_block.get("channelCount"),
                    "sampleRate": capture_constraints_block.get("sampleRate"),
                },
            }
            if not ctx.policy_snapshot_logged:
                self._bus("policy.snapshot", snapshot_payload, sid=ctx.sid)
                ctx.policy_snapshot_logged = True
            self._bus(
                "policy.deprecation_report",
                {
                    "version": version,
                    "mapped_legacy_keys": mapped_hits,
                    "server_starts_input": server_starts_input,
                    "warmup_ms": warmup_ms,
                    "cold_start_grace_ms": cold_start_grace_ms,
                },
                sid=ctx.sid,
            )
            ctx.policy_snapshot_fingerprint = fingerprint
        except Exception:  # pragma: no cover - defensive logging
            _log.exception("evt=policy_snapshot_emit_failed sid=%s", ctx.sid)

    def _policy(self, ctx: AdapterContext) -> Dict[str, Any]:
        if isinstance(ctx.policy, Mapping):
            return dict(ctx.policy)
        return {}

    def _allow_capture_during_tts(self, ctx: AdapterContext) -> bool:
        policy = self._policy(ctx)
        audio_block = policy.get("audio") if isinstance(policy, Mapping) else None
        allow_capture = bool(
            audio_block.get("allow_capture_during_tts") if isinstance(audio_block, Mapping) else False
        )

        recorder_block = None
        policy_block = policy.get("policy") if isinstance(policy, Mapping) else None
        if isinstance(policy_block, Mapping):
            recorder_candidate = policy_block.get("recorder")
            if isinstance(recorder_candidate, Mapping):
                recorder_block = recorder_candidate
        recorder_candidate = policy.get("recorder") if isinstance(policy, Mapping) else None
        if recorder_block is None and isinstance(recorder_candidate, Mapping):
            recorder_block = recorder_candidate

        mute_during_tts = bool(
            recorder_block.get("mute_send_during_tts") if isinstance(recorder_block, Mapping) else False
        )

        return allow_capture and not mute_during_tts

    def _replace_policy(self, ctx: AdapterContext, payload: Mapping[str, Any]) -> None:
        legacy_hits: List[tuple[tuple[str, ...], tuple[str, ...]]] = []
        try:
            normalized = normalize_policy(payload, legacy_hits=legacy_hits)
        except Exception:  # pragma: no cover - defensive logging
            _log.exception("evt=policy_normalize_failed sid=%s", ctx.sid)
            return
        ctx.policy = normalized
        ctx.session.policy = normalized
        ctx.policy_warning_logged = False
        self._emit_policy_snapshot(ctx, legacy_hits)

    @staticmethod
    def _coerce_non_negative_int(value: Any, default: int) -> int:
        try:
            candidate = int(value)
        except (TypeError, ValueError):
            return default
        if candidate < 0:
            return default
        return candidate

    @staticmethod
    def _coerce_float(value: Any, default: float | None) -> float | None:
        try:
            candidate = float(value)
        except (TypeError, ValueError):
            return default
        if math.isnan(candidate) or math.isinf(candidate):
            return default
        return candidate

    @staticmethod
    def _set_client_vad_state(ctx: AdapterContext, speech: bool, now_ms: int) -> None:
        if speech == ctx.client_vad_speech and ctx.client_vad_since_ms is not None:
            return
        ctx.client_vad_speech = speech
        ctx.client_vad_since_ms = now_ms
        if speech:
            ctx.client_vad_last_speech_end_ms = None
        else:
            ctx.client_vad_last_speech_end_ms = now_ms

    def _resolve_server_vad_policy(self, ctx: AdapterContext) -> Dict[str, Any]:
        policy = dict(_SERVER_VAD_DEFAULT_POLICY)
        if FEATURE_LEGACY_POLICY and isinstance(ctx.policy_snapshot, Mapping):
            snapshot = ctx.policy_snapshot
            policy_block = snapshot.get("policy") if isinstance(snapshot, Mapping) else None
            vad_source = (
                policy_block.get("vad") if isinstance(policy_block, Mapping) else None
            )
        else:
            policy_block = ctx.policy if isinstance(ctx.policy, Mapping) else None
            vad_source = policy_block.get("vad") if isinstance(policy_block, Mapping) else None

        if isinstance(vad_source, Mapping):
            server_block = vad_source.get("server")
            if isinstance(server_block, Mapping):
                enable_flag = server_block.get("enable")
                if isinstance(enable_flag, bool):
                    policy["enable"] = enable_flag
                policy["min_speech_ms"] = self._coerce_non_negative_int(
                    server_block.get("min_speech_ms"), policy["min_speech_ms"]
                )
                policy["min_silence_ms"] = self._coerce_non_negative_int(
                    server_block.get("min_silence_ms"), policy["min_silence_ms"]
                )
                policy["eot_silence_ms"] = self._coerce_non_negative_int(
                    server_block.get("eot_silence_ms"), policy["eot_silence_ms"]
                )
                fuse_mode_value = server_block.get("fuse_mode")
                if isinstance(fuse_mode_value, str):
                    normalized = fuse_mode_value.strip().lower()
                    if normalized in {"and", "or", "weighted"}:
                        policy["fuse_mode"] = normalized
                weight_client = self._coerce_float(
                    server_block.get("weight_client"), policy["weight_client"]
                )
                if weight_client is not None:
                    policy["weight_client"] = max(0.0, weight_client)
                weight_vendor = self._coerce_float(
                    server_block.get("weight_vendor"), policy["weight_vendor"]
                )
                if weight_vendor is not None:
                    policy["weight_vendor"] = max(0.0, weight_vendor)
                threshold_value = self._coerce_float(
                    server_block.get("energy_threshold_dbfs"),
                    policy["energy_threshold_dbfs"],
                )
                if threshold_value is not None:
                    policy["energy_threshold_dbfs"] = threshold_value
        return policy

    def _publish_server_vad_event(
        self, ctx: AdapterContext, event_type: str, meta: Mapping[str, Any]
    ) -> None:
        try:
            payload = {
                "schema_version": "1",
                "type": event_type,
                "sid": ctx.sid,
                "who": "server",
                "source": "ws_server",
                "meta": dict(meta),
            }
            bus.publish(payload)
        except Exception:  # pragma: no cover - defensive logging
            _log.exception("evt=server_vad_event_publish_failed sid=%s event=%s", ctx.sid, event_type)

    def _update_server_vad_state(
        self,
        ctx: AdapterContext,
        energy_db: float,
        now_ms: int,
        policy: Mapping[str, Any],
    ) -> None:
        threshold = float(policy.get("energy_threshold_dbfs", _SERVER_VAD_DEFAULT_POLICY["energy_threshold_dbfs"]))
        min_speech_ms = max(0, int(policy.get("min_speech_ms", _SERVER_VAD_DEFAULT_POLICY["min_speech_ms"])))
        min_silence_ms = max(0, int(policy.get("min_silence_ms", _SERVER_VAD_DEFAULT_POLICY["min_silence_ms"])))
        above_threshold = energy_db > threshold
        ctx.server_vad_energy_db = energy_db
        if above_threshold:
            ctx.server_vad_silence_candidate_ms = None
            if ctx.server_vad_candidate_start_ms is None:
                ctx.server_vad_candidate_start_ms = now_ms
            if not ctx.session.server_vad_speech:
                candidate = ctx.server_vad_candidate_start_ms or now_ms
                if now_ms - candidate >= min_speech_ms:
                    ctx.session.server_vad_speech = True
                    ctx.session.server_vad_since_ms = float(candidate)
                    ctx.server_vad_candidate_start_ms = None
                    meta = {
                        "energy_db": energy_db,
                        "threshold_db": threshold,
                        "since_ms": candidate,
                    }
                    self._publish_server_vad_event(ctx, "server.vad.speech_start", meta)
                    state_meta = dict(meta)
                    state_meta["speech"] = True
                    self._publish_server_vad_event(ctx, "server.vad.state", state_meta)
        else:
            ctx.server_vad_candidate_start_ms = None
            if ctx.session.server_vad_speech and ctx.server_vad_silence_candidate_ms is None:
                ctx.server_vad_silence_candidate_ms = now_ms
            if ctx.session.server_vad_speech:
                silence_candidate = ctx.server_vad_silence_candidate_ms or now_ms
                if now_ms - silence_candidate >= min_silence_ms:
                    previous_start = ctx.session.server_vad_since_ms
                    duration_ms = 0
                    if previous_start is not None:
                        duration_ms = max(0, now_ms - int(previous_start))
                    ctx.session.server_vad_speech = False
                    ctx.session.server_vad_since_ms = float(now_ms)
                    ctx.server_vad_silence_candidate_ms = None
                    meta = {
                        "energy_db": energy_db,
                        "threshold_db": threshold,
                        "since_ms": now_ms,
                        "duration_ms": duration_ms,
                    }
                    self._publish_server_vad_event(ctx, "server.vad.speech_end", meta)
                    state_meta = {
                        "speech": False,
                        "energy_db": energy_db,
                        "threshold_db": threshold,
                        "since_ms": now_ms,
                    }
                    self._publish_server_vad_event(ctx, "server.vad.state", state_meta)
            else:
                if ctx.session.server_vad_since_ms is None:
                    ctx.session.server_vad_since_ms = float(now_ms)

    def _maybe_update_server_vad(self, ctx: AdapterContext, chunk: bytes, now_ms: int) -> None:
        policy = self._resolve_server_vad_policy(ctx)
        ctx.session.last_pcm_ms = float(now_ms)
        if not policy.get("enable", True):
            ctx.session.server_vad_speech = False
            if ctx.session.server_vad_since_ms is None:
                ctx.session.server_vad_since_ms = float(now_ms)
            ctx.server_vad_candidate_start_ms = None
            ctx.server_vad_silence_candidate_ms = None
            ctx.server_vad_energy_db = _DB_FLOOR
            return
        energy_db = _pcm16_rms_dbfs(chunk)
        self._update_server_vad_state(ctx, energy_db, now_ms, policy)

    def _ensure_vad_fusion_task(self, ctx: AdapterContext) -> None:
        task = ctx.vad_fusion_task
        if task is not None and not task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            ctx.vad_fusion_task = loop.create_task(self._run_vad_fusion(ctx))
        else:
            ctx.vad_fusion_task = asyncio.create_task(self._run_vad_fusion(ctx))

    async def _run_vad_fusion(self, ctx: AdapterContext) -> None:
        interval = max(0.05, _SERVER_VAD_EVAL_INTERVAL_MS / 1000.0)
        try:
            while True:
                await asyncio.sleep(interval)
                if ctx.ws_send is None:
                    break
                if not ctx.session.eot_armed:
                    continue
                if ctx.session.tts_active:
                    continue
                if ctx.session.asr_state != "open":
                    continue
                if ctx.session.last_pcm_ms is None:
                    continue
                now_ms = self._now_ms()
                policy = self._resolve_server_vad_policy(ctx)
                client_silence_ms = self._compute_client_silence_ms(ctx, now_ms)
                server_silence_ms = self._compute_server_silence_ms(
                    ctx, now_ms, policy, client_silence_ms
                )
                vendor_idle_ms = self._compute_vendor_idle_ms(ctx, now_ms)
                min_silence_ms = max(0, int(policy.get("min_silence_ms", 0)))
                eot_silence_ms = max(0, int(policy.get("eot_silence_ms", 0)))
                client_ok = client_silence_ms >= min_silence_ms
                server_ok = (not policy.get("enable", True)) or server_silence_ms >= min_silence_ms
                silence_ok = client_ok and server_ok
                vendor_ok = vendor_idle_ms >= eot_silence_ms
                denom_client = max(1, min_silence_ms)
                client_norm = _clamp(client_silence_ms / denom_client, 0.0, 1.0)
                if policy.get("enable", True):
                    server_norm = _clamp(server_silence_ms / denom_client, 0.0, 1.0)
                    combined_norm = min(client_norm, server_norm)
                else:
                    combined_norm = client_norm
                denom_vendor = max(1, eot_silence_ms)
                vendor_norm = _clamp(vendor_idle_ms / denom_vendor, 0.0, 1.0)
                fuse_mode = str(policy.get("fuse_mode", "and")).strip().lower()
                if fuse_mode not in {"and", "or", "weighted"}:
                    fuse_mode = "and"
                should_close = False
                score: float | None = None
                weight_client: float | None = None
                weight_vendor: float | None = None
                if fuse_mode == "and":
                    should_close = silence_ok and vendor_ok
                elif fuse_mode == "or":
                    should_close = silence_ok or vendor_ok
                elif fuse_mode == "weighted":
                    weight_client = float(policy.get("weight_client", 0.5) or 0.0)
                    weight_vendor = float(policy.get("weight_vendor", 0.5) or 0.0)
                    score = (weight_client * combined_norm) + (weight_vendor * vendor_norm)
                    should_close = score >= 1.0 and (combined_norm > 0.0 or vendor_norm > 0.0)
                if not should_close:
                    continue
                min_stream_ms = 1200
                bytes_streamed = ctx.asr_bytes_sent
                if isinstance(bytes_streamed, int) and bytes_streamed >= 0:
                    bytes_streamed = int(bytes_streamed)
                else:
                    bytes_streamed = 0
                if isinstance(ctx.asr_opened_ms, int):
                    stream_age_ms = max(0, self._now_ms() - ctx.asr_opened_ms)
                else:
                    stream_age_ms = min_stream_ms
                base_meta: Dict[str, Any] = {
                    "sid": ctx.sid,
                    "fuse_mode": fuse_mode,
                    "client_silence_ms": int(client_silence_ms),
                    "server_silence_ms": int(server_silence_ms),
                    "vendor_idle_ms": int(vendor_idle_ms),
                    "min_silence_ms": int(min_silence_ms),
                    "eot_silence_ms": int(eot_silence_ms),
                    "bytes_streamed": int(bytes_streamed),
                    "ms_since_first_packet": int(stream_age_ms),
                    "min_stream_ms": int(min_stream_ms),
                    "silence_ok": bool(silence_ok),
                    "vendor_ok": bool(vendor_ok),
                }
                if score is not None:
                    base_meta["weighted_score"] = float(score)
                if weight_client is not None:
                    base_meta["weight_client"] = float(weight_client)
                if weight_vendor is not None:
                    base_meta["weight_vendor"] = float(weight_vendor)
                if bytes_streamed <= 0 or stream_age_ms < min_stream_ms:
                    if not ctx.asr_silence_hold_logged:
                        hold_meta = dict(base_meta)
                        hold_meta["decision"] = "hold"
                        hold_meta["hold_reason"] = "insufficient_audio"
                        self._bus("asr.silence_hold", hold_meta, sid=ctx.sid)
                        ctx.asr_silence_hold_logged = True
                    continue
                reason = "silence_final"
                if silence_ok and not vendor_ok:
                    reason = "silence_only"
                elif not silence_ok and vendor_ok:
                    reason = "policy_close"
                if not ctx.asr_silence_eot_logged:
                    eot_meta = dict(base_meta)
                    eot_meta["decision"] = "eot"
                    eot_meta["close_reason"] = reason
                    self._bus("asr.silence_eot", eot_meta, sid=ctx.sid)
                    ctx.asr_silence_eot_logged = True
                await self._handle_vad_eot(
                    ctx,
                    reason,
                    fuse_mode,
                    client_silence_ms,
                    vendor_idle_ms,
                    server_silence_ms,
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive logging
            _log.exception("evt=server_vad_fusion_loop_error sid=%s", ctx.sid)
        finally:
            ctx.vad_fusion_task = None

    def _compute_client_silence_ms(self, ctx: AdapterContext, now_ms: int) -> int:
        if ctx.client_vad_speech:
            return 0
        if ctx.client_vad_since_ms is not None and not ctx.client_vad_speech:
            return max(0, now_ms - int(ctx.client_vad_since_ms))
        if ctx.client_vad_last_speech_end_ms is not None:
            return max(0, now_ms - int(ctx.client_vad_last_speech_end_ms))
        if ctx.session.last_pcm_ms is not None:
            return max(0, now_ms - int(ctx.session.last_pcm_ms))
        return 0

    def _compute_server_silence_ms(
        self,
        ctx: AdapterContext,
        now_ms: int,
        policy: Mapping[str, Any],
        client_silence_ms: int,
    ) -> int:
        if not policy.get("enable", True):
            if ctx.session.server_vad_since_ms is None:
                ctx.session.server_vad_since_ms = float(now_ms)
            return client_silence_ms
        if ctx.session.server_vad_speech:
            return 0
        if ctx.session.server_vad_since_ms is not None:
            return max(0, now_ms - int(ctx.session.server_vad_since_ms))
        return client_silence_ms

    def _compute_vendor_idle_ms(self, ctx: AdapterContext, now_ms: int) -> int:
        last_vendor = ctx.session.last_vendor_activity_ms
        if last_vendor is None:
            last_vendor = ctx.session.last_pcm_ms
        if last_vendor is None:
            return 0
        return max(0, now_ms - int(last_vendor))

    async def _handle_vad_eot(
        self,
        ctx: AdapterContext,
        reason: str,
        fuse_mode: str,
        client_silence_ms: int,
        vendor_idle_ms: int,
        server_silence_ms: int,
    ) -> None:
        if not ctx.session.eot_armed:
            return
        asr_client = getattr(ctx.session, "asr", None)
        bytes_streamed = int(getattr(asr_client, "bytes_streamed", 0) or 0)
        ms_since_first = int(getattr(asr_client, "ms_since_first_packet", 0) or 0)
        if bytes_streamed <= 0 or ms_since_first < 1200:
            # no real audio yet → hold; let client VAD open when speech appears
            return
        ctx.session.eot_armed = False
        _log.info(
            "evt=vad_eot sid=%s reason=%s fuse_mode=%s client_silence_ms=%d vendor_idle_ms=%d server_silence_ms=%d",
            ctx.sid,
            reason,
            fuse_mode,
            client_silence_ms,
            vendor_idle_ms,
            server_silence_ms,
        )
        meta = {
            "reason": reason,
            "fuse_mode": fuse_mode,
            "client_silence_ms": int(client_silence_ms),
            "vendor_idle_ms": int(vendor_idle_ms),
            "server_silence_ms": int(server_silence_ms),
        }
        await self._publish("asr.turn_ended", ctx.sid, meta)
        send = ctx.ws_send
        turn_end_payload = self._prepare_asr_turn_end(ctx, "eos")
        if send is not None:
            try:
                if turn_end_payload is not None:
                    await self._send_json(send, ctx.sid, turn_end_payload)
                if ctx.turn_active:
                    try:
                        await self._send_json(send, ctx.sid, {"type": "turn.end"})
                    except Exception:  # pragma: no cover - defensive logging
                        _log.warning(
                            "evt=turn_end_send_failed sid=%s reason=vad_eot",
                            ctx.sid,
                            exc_info=True,
                        )
                    finally:
                        ctx.turn_active = False
                await self._send_json(send, ctx.sid, {"type": "asr.close", "reason": "eot"})
            except Exception:  # pragma: no cover - defensive logging
                _log.warning("evt=vad_eot_send_failed sid=%s", ctx.sid, exc_info=True)
        else:
            ctx.turn_active = False
        await self._close_asr(ctx, reason="eot")

    def _handle_client_telemetry(
        self, ctx: AdapterContext, event_name: str, meta: Mapping[str, Any]
    ) -> None:
        now_ms = self._now_ms()
        if event_name == "client.vad.speech_start":
            self._set_client_vad_state(ctx, True, now_ms)
            ctx.client_vad_last_event_ms = now_ms
            conf = self._coerce_float(meta.get("conf"), ctx.client_vad_confidence)
            if conf is not None:
                ctx.client_vad_confidence = _clamp(conf, 0.0, 1.0)
            energy = self._coerce_float(meta.get("energyDb"), ctx.client_vad_energy_db)
            if energy is not None:
                ctx.client_vad_energy_db = energy
            noise = self._coerce_float(meta.get("noiseDb"), ctx.client_vad_noise_db)
            if noise is not None:
                ctx.client_vad_noise_db = noise
        elif event_name == "client.vad.speech_end":
            self._set_client_vad_state(ctx, False, now_ms)
            ctx.client_vad_last_event_ms = now_ms
            ctx.client_vad_last_speech_end_ms = now_ms
        elif event_name == "client.vad.state":
            speech_value = meta.get("speech")
            if isinstance(speech_value, bool):
                self._set_client_vad_state(ctx, speech_value, now_ms)
            ctx.client_vad_last_event_ms = now_ms
            conf = self._coerce_float(meta.get("conf"), ctx.client_vad_confidence)
            if conf is not None:
                ctx.client_vad_confidence = _clamp(conf, 0.0, 1.0)
            energy = self._coerce_float(meta.get("energyDb"), ctx.client_vad_energy_db)
            if energy is not None:
                ctx.client_vad_energy_db = energy
            noise = self._coerce_float(meta.get("noiseDb"), ctx.client_vad_noise_db)
            if noise is not None:
                ctx.client_vad_noise_db = noise
        elif event_name in {"client.vad.gate", "client.vad.gate_heartbeat"}:
            ctx.client_vad_last_event_ms = now_ms
            if event_name == "client.vad.gate":
                self._maybe_trigger_vad_eot_from_client_gate(ctx, meta, now_ms)
        elif event_name in {"client.appstate.delta", "client.appstate.heartbeat"}:
            speech_value = meta.get("vadSpeech")
            if isinstance(speech_value, bool):
                self._set_client_vad_state(ctx, speech_value, now_ms)
            ctx.client_vad_last_event_ms = now_ms
            conf = self._coerce_float(meta.get("vadConfidence"), ctx.client_vad_confidence)
            if conf is not None:
                ctx.client_vad_confidence = _clamp(conf, 0.0, 1.0)
            energy = self._coerce_float(meta.get("vadEnergyDb"), ctx.client_vad_energy_db)
            if energy is not None:
                ctx.client_vad_energy_db = energy
            noise = self._coerce_float(meta.get("vadNoiseDb"), ctx.client_vad_noise_db)
            if noise is not None:
                ctx.client_vad_noise_db = noise
        else:
            ctx.client_vad_last_event_ms = now_ms

    def _maybe_trigger_vad_eot_from_client_gate(
        self,
        ctx: AdapterContext,
        meta: Mapping[str, Any],
        now_ms: int,
    ) -> None:
        """Use client VAD gate 'pause/silence' as an end-of-turn trigger."""

        # Only act if ASR is actually open and EOT is armed
        if ctx.ws_send is None:
            return
        if not ctx.session.eot_armed or ctx.session.asr_state != "open":
            return

        action = meta.get("action")
        reason = meta.get("reason") or ""
        if action != "pause":
            return
        # Only treat silence-based pauses as EOT; ignore policy resets etc.
        if "silence" not in str(reason).lower():
            return

        # Reuse the same policy + timing math as the server VAD fusion
        policy = self._resolve_server_vad_policy(ctx)
        client_silence_ms = self._compute_client_silence_ms(ctx, now_ms)
        server_silence_ms = self._compute_server_silence_ms(
            ctx,
            now_ms,
            policy,
            client_silence_ms,
        )
        vendor_idle_ms = self._compute_vendor_idle_ms(ctx, now_ms)

        fuse_mode = str(policy.get("fuse_mode", "and")).strip().lower()
        if fuse_mode not in {"and", "or", "weighted"}:
            fuse_mode = "and"

        # Decide if we *should* close; mirror _run_vad_fusion’s rules, but run once
        min_silence_ms = max(0, int(policy.get("min_silence_ms", 0)))
        eot_silence_ms = max(0, int(policy.get("eot_silence_ms", 0)))
        client_ok = client_silence_ms >= min_silence_ms
        server_ok = (not policy.get("enable", True)) or server_silence_ms >= min_silence_ms
        silence_ok = client_ok and server_ok
        vendor_ok = vendor_idle_ms >= eot_silence_ms

        should_close = False
        if fuse_mode == "and":
            should_close = silence_ok and vendor_ok
        elif fuse_mode == "or":
            should_close = silence_ok or vendor_ok
        else:  # weighted
            denom_client = max(1, min_silence_ms)
            denom_vendor = max(1, eot_silence_ms)
            client_norm = min(1.0, client_silence_ms / denom_client)
            vendor_norm = min(1.0, vendor_idle_ms / denom_vendor)
            weight_client = float(policy.get("weight_client", 0.5) or 0.0)
            weight_vendor = float(policy.get("weight_vendor", 0.5) or 0.0)
            score = (weight_client * client_norm) + (weight_vendor * vendor_norm)
            should_close = score >= 1.0 and (client_norm > 0.0 or vendor_norm > 0.0)

        if not should_close:
            return

        async def _run_vad_eot() -> None:
            await self._handle_vad_eot(
                ctx,
                "client_gate_silence",
                fuse_mode,
                client_silence_ms,
                vendor_idle_ms,
                server_silence_ms,
            )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            loop.create_task(_run_vad_eot())
        else:
            asyncio.create_task(_run_vad_eot())

    def _emit_server_vendor_activity(
        self, ctx: AdapterContext, event_type: str, event: Mapping[str, Any], now_ms: int
    ) -> None:
        meta: Dict[str, Any] = {
            "kind": "partial" if event_type == EVT_ASR_PARTIAL else "final",
            "ts_ms": now_ms,
        }
        text_value = event.get("text")
        if isinstance(text_value, str):
            meta["text_length"] = len(text_value)
        req_id = event.get("req_id")
        if isinstance(req_id, str) and req_id:
            meta["req_id"] = req_id
        self._publish_server_vad_event(ctx, "server.vendor_activity", meta)

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
            "evt=await_user_emit sid=%s req_id=%s start_on_ready=%s",
            sid,
            req_value or "",
            start_on_ready,
        )

    def _publish_pending_start_listening(
        self, ctx: AdapterContext, telemetry_bus: Any
    ) -> None:
        payload = ctx.pending_start_listening
        if not isinstance(payload, dict):
            return
        frame = dict(payload)
        ctx.pending_start_listening = None
        was_sent = getattr(ctx, "pending_start_listening_sent", False)
        ctx.pending_start_listening_sent = False
        if was_sent:
            return
        outbox = ctx.outbox
        if outbox is not None:
            try:
                outbox.put_nowait(frame)
            except asyncio.QueueFull:
                now = outbox.qsize()
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
        else:
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
        ctx.await_user_vad_check_pending = True

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

        offered_subprotocols = [
            proto for proto in scope.get("subprotocols") or [] if isinstance(proto, str)
        ]
        needed = {CHAT_V2_SUBPROTOCOL, CHAT_MSGPACK_SUBPROTOCOL}
        _log.info(
            "evt=ws_subs subprotocols=%r need_any=%r",
            offered_subprotocols,
            needed,
        )
        msgpack_supported = msgpack is not None
        selected_subprotocol: Optional[str] = None
        fallback_reason: Optional[str] = None
        if msgpack_supported and CHAT_MSGPACK_SUBPROTOCOL in offered_subprotocols:
            selected_subprotocol = CHAT_MSGPACK_SUBPROTOCOL
        elif CHAT_MSGPACK_SUBPROTOCOL in offered_subprotocols and CHAT_V2_SUBPROTOCOL not in offered_subprotocols:
            _log.warning(
                "evt=ws_accept_reject code=4401 reason=msgpack_unavailable path=%s",
                path_qs,
            )
            await self._reject_subprotocol(send)
            return
        elif CHAT_V2_SUBPROTOCOL in offered_subprotocols:
            selected_subprotocol = CHAT_V2_SUBPROTOCOL
            if not msgpack_supported and CHAT_MSGPACK_SUBPROTOCOL in offered_subprotocols:
                fallback_reason = "msgpack_unavailable"
        else:
            _log.warning(
                "evt=ws_accept_reject code=4401 reason=bad_subprotocol path=%s", path_qs
            )
            await self._reject_subprotocol(send)
            return

        if fallback_reason:
            _log.info(
                "evt=ws_subprotocol_fallback reason=%s offered=%r", fallback_reason, offered_subprotocols
            )

        selected_codec: Literal["json", "msgpack"]
        if selected_subprotocol == CHAT_MSGPACK_SUBPROTOCOL:
            selected_codec = "msgpack"
        else:
            selected_codec = "json"

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

        accept_headers: list[tuple[bytes, bytes]] = []
        if selected_codec == "json" and self._client_offers_permessage_deflate(headers):
            accept_headers.append((b"sec-websocket-extensions", _PERMESSAGE_DEFLATE_HEADER))

        accept_message: dict[str, Any] = {
            "type": "websocket.accept",
            "subprotocol": selected_subprotocol,
        }
        if accept_headers:
            accept_message["headers"] = accept_headers

        _log.info("evt=ws_accept subprotocol='%s' codec=%s", selected_subprotocol, selected_codec)
        await send(accept_message)

        self._emit_session_step(
            sid,
            "ws.accepted",
            summary="Accepted WebSocket upgrade",
            meta={"subprotocol": selected_subprotocol, "codec": selected_codec},
            source="ws.accept",
        )

        # ---- BEGIN RUNTIME BANNER ----
        try:
            adapter_file = __file__
            engine_file = None
            if self.engine is not None:
                eng_mod = sys.modules.get(self.engine.__class__.__module__)
                engine_file = getattr(eng_mod, "__file__", None)

            build_id = current_build_id()
            host = socket.gethostname()
            pid = os.getpid()
            cwd = os.getcwd()

            offered = list(offered_subprotocols)
            selected = selected_subprotocol

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
                "control_codec": selected_codec,
                "permessage_deflate": bool(accept_headers),
                "adapter_file": adapter_file,
                "engine_file": engine_file,
            }

            banner_ctx = AdapterContext(
                sid=sid,
                headers=dict(headers),
                principal=principal,
                user_id=sub,
                is_admin=is_admin,
            )
            banner_ctx.control_codec = selected_codec
            await self._send_json(send, sid, banner, ctx=banner_ctx)
            _log.info(
                "evt=server_banner build_id=%s host=%s pid=%d path=%s subproto=%s adapter=%s engine=%s",
                build_id,
                host,
                pid,
                scope.get("path"),
                selected,
                adapter_file,
                engine_file,
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
                    "codec": selected_codec,
                },
            )
        except Exception:
            _log.exception("evt=server_banner_emit_failed")
        # ---- END RUNTIME BANNER ----

        now_ms = int(time.time() * 1000)
        info_frame: Dict[str, Any] = {
            "type": "info",
            "protocol": selected_subprotocol,
            "control_codec": selected_codec,
            "sid": sid,
            "ts_ms": now_ms,
            "build_id": current_build_id(),
        }
        info_frame["meta"] = {"sid": sid}
        policy_snapshot = self._policy_snapshot() if FEATURE_LEGACY_POLICY else None
        legacy_hits: List[tuple[tuple[str, ...], tuple[str, ...]]] = []
        session_policy_v2 = self._build_session_policy(legacy_hits=legacy_hits)
        allowed_asr_vendors = ["gcp"]
        snapshot_mapping: Mapping[str, Any] | None
        if FEATURE_LEGACY_POLICY and isinstance(policy_snapshot, Mapping):
            snapshot_mapping = policy_snapshot
        else:
            snapshot_mapping = session_policy_v2 if isinstance(session_policy_v2, Mapping) else None

        provisional_ctx = AdapterContext(
            sid=sid,
            headers=dict(headers),
            principal=principal,
            user_id=sub,
            is_admin=is_admin,
        )
        provisional_ctx.control_codec = selected_codec
        if FEATURE_LEGACY_POLICY and isinstance(policy_snapshot, dict):
            provisional_ctx.policy_snapshot = dict(policy_snapshot)
        elif FEATURE_LEGACY_POLICY:
            provisional_ctx.policy_snapshot = policy_snapshot
        else:
            provisional_ctx.policy_snapshot = None
        provisional_ctx.policy = dict(session_policy_v2)
        provisional_ctx.session.policy = provisional_ctx.policy
        provisional_ctx.allowed_asr_vendors = list(allowed_asr_vendors)
        selected_vendor = "gcp"
        selection_reason = "pcm16_only"
        if FEATURE_LEGACY_POLICY and policy_snapshot:
            self._log_policy_flags(sid, policy_snapshot)
        info_frame["policy"] = self._policy_for_client(session_policy_v2)
        await self._send_json(send, sid, info_frame, ctx=provisional_ctx)
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
        ctx.control_codec = selected_codec
        if FEATURE_LEGACY_POLICY and isinstance(policy_snapshot, dict):
            ctx.policy_snapshot = dict(policy_snapshot)
        elif FEATURE_LEGACY_POLICY:
            ctx.policy_snapshot = policy_snapshot
        else:
            ctx.policy_snapshot = None
        ctx.policy = dict(session_policy_v2)
        ctx.session.policy = ctx.policy
        ctx.last_client_activity_ms = now_ms
        ctx.allowed_asr_vendors = list(allowed_asr_vendors)
        if FEATURE_LEGACY_POLICY and isinstance(ctx.policy_snapshot, Mapping):
            snapshot_mapping = ctx.policy_snapshot
        else:
            snapshot_mapping = session_policy_v2 if isinstance(session_policy_v2, Mapping) else None
        ctx.asr_vendor = selected_vendor
        allowed_display = ",".join(ctx.allowed_asr_vendors)
        _log.info(
            "asr_vendor_selected primary=%s allowed=%s reason=%s",
            ctx.asr_vendor,
            allowed_display,
            selection_reason,
        )
        ctx.asr_vendor_logged = True
        ctx.audio_pipeline_mode = self._resolve_audio_pipeline_mode(snapshot_mapping)
        if ctx.asr_vendor == "gcp":
            ctx.audio_pipeline_mode = "pcm16"
        mode = ctx.audio_pipeline_mode or "pcm16"
        ctx.session_capture_policy = self._session_capture_policy_for_mode(mode)
        ctx.ws_send = send
        self._ensure_ingress_tick_timer(ctx)

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

            self._contexts[ctx.sid] = ctx
            self._emit_policy_snapshot(ctx, legacy_hits)

            _log.info("evt=ws_open sid=%s", ctx.sid)
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
                self._flush_ingress_tick(ctx)
                t_close_ms = self._now_ms()
                ctx.session.closed_at_ms = t_close_ms
                await self._publish(
                    EVT_SESSION_TRANSPORT_CLOSED,
                    ctx.sid,
                    {"code": close_code, "reason": close_reason, "t_close_ms": t_close_ms},
                )
                await self._close_asr(ctx, reason="transport_closed")
                await self._cleanup_outbound(ctx)
                self._stop_asr_ready_tracker(ctx)
                await self._invoke_engine("on_close", ctx.sid, close_code, close_reason)
                vendor_meta = {
                    "vendor": ctx.asr_vendor or "gcp",
                    "bytes": ctx.asr_bytes_sent,
                }
                await self._publish(ASR_VENDOR_BYTES_TOTAL, ctx.sid, vendor_meta)
                rollup_meta = dict(vendor_meta)
                rollup_meta["state"] = ctx.session.asr_state
                if ctx.asr_close_reason:
                    rollup_meta["reason"] = ctx.asr_close_reason
                await self._publish(ASR_ROLLUP, ctx.sid, rollup_meta)
                await self._publish(
                    AUDIO_WIRE_ROLLUP,
                    ctx.sid,
                    {"ingress_bytes": ctx.ingress_bytes, "packets": ctx.ingress_packets},
                )
                after_close_ms = max(0, self._now_ms() - t_close_ms)
                await self._publish(
                    SESSION_TEARDOWN_COMPLETE,
                    ctx.sid,
                    {"after_close_ms": after_close_ms},
                )
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
                ctx.ws_send = None
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
        detail = "use chat.v2 or chip-msgpack"
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
        self,
        data: str,
        ctx: AdapterContext,
        send: Callable[[dict], Awaitable[None]],
        *,
        codec: Literal["json", "msgpack"] = "json",
        raw_bytes: bytes | None = None,
        predecoded: Mapping[str, Any] | None = None,
    ) -> _HandleResult:
        try:
            if codec == "msgpack" and raw_bytes is not None:
                payload_bytes = raw_bytes
            else:
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
                    "codec": codec,
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
                "codec": codec,
            },
        }

        if codec == "msgpack":
            try:
                preview_bytes = data.encode("utf-8", "replace")
            except Exception:
                preview_bytes = None
            preview = (
                self._make_preview_from_bytes(preview_bytes)
                if preview_bytes is not None
                else None
            )
        else:
            preview = self._make_preview_from_bytes(payload_bytes)
        if preview is not None:
            meta["ws"]["preview"] = preview

        frame_payload: Optional[Dict[str, Any]] = None

        if byte_count > self.text_limit_bytes:
            meta["error"] = "frame_too_large"
            await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta)
            await self._send_error(send, ctx.sid, "frame_too_large", "Text frame exceeds limit")
            return self._HandleResult(False, 1009, "frame_too_large")

        if predecoded is not None and isinstance(predecoded, Mapping):
            frame = dict(predecoded)
        else:
            try:
                frame = json.loads(data if codec == "msgpack" else payload_bytes.decode("utf-8"))
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
            await self._publish_json_recv(ctx, meta, frame_payload)
            await self._send_error(send, ctx.sid, "schema_invalid", "Frame missing type field")
            return self._HandleResult(True)

        frame_type = raw_type
        meta["frame_type"] = frame_type
        now_ms = int(time.time() * 1000)
        ctx.last_client_activity_ms = now_ms

        if frame_type in (
            "client.metrics",
            "client.log",
            "client.pong",
        ):
            await self._publish(
                EVT_WS_JSON_RECV,
                ctx.sid,
                {"type": frame_type, "ok": True, "ws": {"dir": "in", "codec": codec}},
            )
            if frame_type == "client.pong":
                ctx.last_client_pong_ms = now_ms
            return self._HandleResult(True)

        if frame_type in (
            "client.ready",
            "client.telemetry",
            "client.diag",
        ):
            await self._publish(
                EVT_WS_JSON_RECV,
                ctx.sid,
                {"type": frame_type, "ok": True, "ws": {"dir": "in", "codec": codec}},
            )
            return self._HandleResult(True)

        if frame_type in ("turn.begin", "turn.end"):
            return self._HandleResult(True)

        if frame_type not in _RATE_LIMIT_EXEMPT_TYPES:
            limited = await self._check_rate_limit(ctx, send)
            if limited is not None:
                return limited

        if frame_type == "client.idle":
            lane = frame.get("lane")
            if lane is not None and not isinstance(lane, str):
                meta["error"] = "schema_invalid"
                await self._publish_json_recv(ctx, meta, frame_payload)
                await self._send_error(
                    send,
                    ctx.sid,
                    "schema_invalid",
                    "client.idle lane must be a string if provided",
                )
                return self._HandleResult(True)

            ts_value = frame.get("ts")
            if ts_value is not None and (
                not isinstance(ts_value, int) or isinstance(ts_value, bool)
            ):
                meta["error"] = "schema_invalid"
                await self._publish_json_recv(ctx, meta, frame_payload)
                await self._send_error(
                    send,
                    ctx.sid,
                    "schema_invalid",
                    "client.idle ts must be an integer if provided",
                )
                return self._HandleResult(True)

            idle_meta: Dict[str, Any] = {}
            if isinstance(lane, str) and lane:
                idle_meta["lane"] = lane
            if isinstance(ts_value, int) and not isinstance(ts_value, bool):
                idle_meta["ts"] = ts_value

            bus.publish(
                {
                    "schema_version": "1",
                    "type": CLIENT_IDLE_TICK,
                    "sid": ctx.sid,
                    "who": "client",
                    "source": "browser",
                    "meta": idle_meta,
                }
            )
            await self._publish_json_recv(ctx, meta, frame_payload)
            return self._HandleResult(True)

        if frame_type == "client.ping":
            await self._publish_json_recv(ctx, meta, frame_payload)
            if now_ms - ctx.last_pong_sent_ms >= PING_MIN_INTERVAL_MS:
                ctx.last_pong_sent_ms = now_ms
                client_ts = frame.get("ts")
                payload = {"type": "server.pong", "ts": now_ms}
                if isinstance(client_ts, int):
                    payload["echo"] = client_ts
                await self._send_json(send, ctx.sid, payload)
            return self._HandleResult(True)

        if frame_type == "client.pong":
            await self._publish_json_recv(ctx, meta, frame_payload)
            ctx.last_client_pong_ms = now_ms
            return self._HandleResult(True)

        if frame_type == "ping":
            await self._publish_json_recv(ctx, meta, frame_payload)
            if now_ms - ctx.last_pong_sent_ms >= PING_MIN_INTERVAL_MS:
                ctx.last_pong_sent_ms = now_ms
                reply_ts = frame.get("t")
                if not isinstance(reply_ts, int):
                    reply_ts = now_ms
                await self._send_json(send, ctx.sid, {"type": "pong", "t": reply_ts})
            return self._HandleResult(True)

        if frame_type in ("input.start", "start_listening", "stop_listening"):
            await self._publish(
                EVT_WS_JSON_RECV,
                ctx.sid,
                {"type": frame_type, "ok": True, "ws": {"dir": "in", "codec": codec}},
            )
            return self._HandleResult(True)

        if frame_type == "input.stop":
            reason_value = frame.get("reason")
            reason = reason_value if isinstance(reason_value, str) and reason_value else "client_turn_stop"
            await self._handle_client_turn_stop(ctx, reason=reason, frame=frame, meta=meta, send=send)
            return self._HandleResult(True)

        if frame_type == "chat.user":
            text = frame.get("text")
            if not isinstance(text, str):
                meta["error"] = "schema_invalid"
                await self._publish_json_recv(ctx, meta, frame_payload)
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
                await self._publish_json_recv(ctx, meta, frame_payload)
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
                await self._publish_json_recv(ctx, meta, frame_payload)
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
                await self._publish_json_recv(ctx, meta, frame_payload)
                await self._send_error(send, ctx.sid, "unknown_type", frame_type)
                return self._HandleResult(True)

            is_valid, hint = validate_frame(frame)
            if not is_valid:
                meta["error"] = "schema_invalid"
                await self._publish_json_recv(ctx, meta, frame_payload)
                detail = hint or "Frame failed validation"
                await self._send_error(send, ctx.sid, "schema_invalid", detail)
                return self._HandleResult(True)

        if frame_type == "asr.open":
            if ctx.session.tts_active:
                ctx.session.queued_arm = True
                await self._publish(
                    ASR_OPEN_QUEUED,
                    ctx.sid,
                    {"reason": "tts_active", "state": ctx.session.asr_state},
                )
            elif not can_open(ctx.session):
                await self._publish(
                    ASR_OPEN_DEDUP,
                    ctx.sid,
                    {"state": ctx.session.asr_state},
                )
                await self._publish_json_recv(ctx, meta, frame_payload)
                await self._send_asr_error(send, ctx, "already_open")
                return self._HandleResult(True)
            else:
                try:
                    self._schedule_asr_open(ctx)
                except Exception:
                    _log.exception("evt=asr_schedule_failed sid=%s", ctx.sid)
                    await self._publish_json_recv(ctx, meta, frame_payload)
                    await self._send_asr_error(send, ctx, "open_failed")
                    return self._HandleResult(True)

        if frame_type == "asr.close":
            raw_seq = frame.get("seq")
            seq = raw_seq if isinstance(raw_seq, str) and raw_seq else uuid.uuid4().hex
            client_sid = frame.get("sid")
            if not isinstance(client_sid, str) or not client_sid:
                client_sid = None
            reason = frame.get("reason")
            if not isinstance(reason, str) or not reason:
                reason = "client_stop"

            server_sid = self._current_asr_sid(ctx)
            ctx.asr_close_reason = reason
            if client_sid and client_sid != server_sid:
                _log.warning(
                    "evt=asr_close_sid_mismatch sid=%s client_sid=%s server_sid=%s",
                    ctx.sid,
                    client_sid,
                    server_sid,
                )

            ack_status = "closed"

            if ctx.session.asr_state in {"opening", "open"} and ctx.session.asr_engine is not None:
                await self._publish(
                    EVT_ASR_CLOSE,
                    ctx.sid,
                    {"state": ctx.session.asr_state},
                )
                try:
                    await self._close_asr(ctx, reason=reason)
                except Exception:
                    ack_status = "error"
                    _log.exception("evt=asr_close_teardown_failed sid=%s", ctx.sid)
                ctx.asr_closed_ack_sent = True
            else:
                ack_status = "already_closed"
                await self._publish(
                    ASR_CLOSE_DEDUP,
                    ctx.sid,
                    {"state": ctx.session.asr_state},
                )
                ctx.asr_closed_ack_sent = True

            ack_frame = self._build_asr_closed_ack(
                ctx,
                seq=seq,
                sid=server_sid,
                reason=reason,
                status=ack_status,
            )
            try:
                await self._send_json(send, ctx.sid, ack_frame)
            finally:
                await self._publish_json_recv(ctx, meta, frame_payload)
            _log.info(
                "evt=asr_close_ack sid=%s seq=%s status=%s final_emitted=%s bytes=%d",
                ctx.sid,
                seq,
                ack_status,
                ctx.asr_final_emitted,
                ctx.asr_bytes_sent,
            )
            return self._HandleResult(True)

        if frame_type == "audio.header":
            await self._publish(
                EVT_WS_JSON_RECV,
                ctx.sid,
                {"type": frame_type, "ok": True, "ws": {"dir": "in", "codec": codec}},
            )
            ctx.client_mic_open = True
            self._schedule_no_audio_watchdog_rearm(ctx, delay_ms=500)
            _log.info("evt=mic_gate_open sid=%s reason=audio_header", ctx.sid)
            expected = {"format": "pcm16", "sample_rate": 16000, "channels": 1}
            fmt = frame.get("format")
            sample_rate = frame.get("sample_rate")
            channels = frame.get("channels")
            normalized_fmt = fmt or expected["format"]
            if normalized_fmt == "pcm":
                normalized_fmt = expected["format"]
            normalized_sr = (
                sample_rate if sample_rate is not None else expected["sample_rate"]
            )
            normalized_ch = (
                channels if channels is not None else expected["channels"]
            )
            if (
                normalized_fmt != expected["format"]
                or normalized_sr != expected["sample_rate"]
                or normalized_ch != expected["channels"]
            ):
                meta["error"] = "bad_header"
                await self._publish_json_recv(ctx, meta, frame_payload)
                await self._send_json(
                    send,
                    ctx.sid,
                    {
                        "type": "asr.error",
                        "code": "bad_header",
                        "expected": expected,
                    },
                )
                return self._HandleResult(True)
            profile = {
                "format": "pcm16",
                "codec": frame.get("codec") or "pcm_s16le",
                "sample_rate": expected["sample_rate"],
                "channels": expected["channels"],
                "container": "raw",
            }
            seq_start = frame.get("seq_start")
            if seq_start is not None:
                if not isinstance(seq_start, int):
                    meta["error"] = "schema_invalid"
                    await self._publish_json_recv(ctx, meta, frame_payload)
                    await self._send_error(
                        send,
                        ctx.sid,
                        "schema_invalid",
                        "audio.header seq_start must be an integer",
                    )
                    return self._HandleResult(False, 1003, "schema_invalid")
                if seq_start < 0:
                    meta["error"] = "schema_invalid"
                    await self._publish_json_recv(ctx, meta, frame_payload)
                    await self._send_error(
                        send,
                        ctx.sid,
                        "schema_invalid",
                        "audio.header seq_start must be >= 0",
                    )
                    return self._HandleResult(False, 1003, "schema_invalid")
                profile["seq_start"] = seq_start
            if ctx.audio_profile is not None:
                existing_profile = (
                    ctx.audio_profile if isinstance(ctx.audio_profile, Mapping) else {}
                )
                existing_codec = existing_profile.get("codec")
                existing_rate = existing_profile.get("sample_rate")
                existing_channels = existing_profile.get("channels")
                existing_format = existing_profile.get("format")

                incoming_codec = profile.get("codec")
                incoming_rate = profile.get("sample_rate")
                incoming_channels = profile.get("channels")
                incoming_format = profile.get("format")

                if (
                    incoming_format == existing_format
                    and incoming_codec == existing_codec
                    and incoming_rate == existing_rate
                    and incoming_channels == existing_channels
                ):
                    _log.info(
                        "evt=audio_header_duplicate sid=%s codec=%s rate_hz=%s ch=%s",
                        ctx.sid,
                        incoming_codec,
                        incoming_rate,
                        incoming_channels,
                    )
                    await self._publish_json_recv(ctx, meta, frame_payload)
                    return self._HandleResult(True)

                meta["error"] = "schema_invalid"
                await self._publish_json_recv(ctx, meta, frame_payload)
                await self._send_error(
                    send,
                    ctx.sid,
                    "schema_invalid",
                    "conflicting audio.header",
                )
                return self._HandleResult(True)
            normalized_header = dict(frame)
            normalized_header.setdefault("format", expected["format"])
            normalized_header.setdefault("sample_rate", expected["sample_rate"])
            normalized_header.setdefault("channels", expected["channels"])
            policy_for_validation: Mapping[str, Any] | None
            if FEATURE_LEGACY_POLICY and isinstance(ctx.policy_snapshot, Mapping):
                policy_for_validation = ctx.policy_snapshot
            else:
                policy_for_validation = (
                    ctx.policy if isinstance(ctx.policy, Mapping) else None
                )
            err = validate_audio_header_against_policy(
                normalized_header, policy_for_validation, ctx.asr_vendor
            )
            if err:
                meta["error"] = "policy_violation"
                await self._publish_json_recv(ctx, meta, frame_payload)
                _log.warning("evt=policy_violation sid=%s err=%s", ctx.sid, err)
                await self._send_error(send, ctx.sid, "policy_violation", err)
                turn_id = getattr(ctx.session, "turn_id", None)
                log_detail = {
                    "session_id": ctx.sid,
                    "code": "policy_violation",
                    "detail": err,
                }
                if isinstance(turn_id, str) and turn_id:
                    log_detail["turn_id"] = turn_id
                self._emit_hub_log(ctx, "policy.violation", log_detail)
                return self._HandleResult(False, 4400, "policy_violation")
            ctx.audio_profile = profile
            ctx.session.audio_profile = profile
            self._arm_no_audio_watchdog(ctx)
            if getattr(ctx, "await_user_vad_check_pending", False):
                ctx.await_user_vad_check_pending = False
                if FEATURE_LEGACY_POLICY:
                    snapshot = (
                        ctx.policy_snapshot
                        if isinstance(ctx.policy_snapshot, Mapping)
                        else None
                    )
                    policy_block = (
                        snapshot.get("policy") if isinstance(snapshot, Mapping) else None
                    )
                    vad_block = (
                        policy_block.get("vad")
                        if isinstance(policy_block, Mapping)
                        else None
                    )
                    auto_active = None
                    if isinstance(vad_block, Mapping) and "auto_vad_active" in vad_block:
                        auto_active = bool(vad_block.get("auto_vad_active"))
                    if auto_active is not True:
                        _log.warning("evt=warn_vad_inactive sid=%s expected=true", ctx.sid)
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
            await self._publish(
                WS_AUDIO_HEADER_ACCEPT,
                ctx.sid,
                audio_meta,
            )
            self._emit_session_step(
                ctx.sid,
                "audio.header.accepted",
                summary="Accepted audio header",
                meta=audio_meta,
                source="ws.audio",
            )
            self._emit_asr_turn_armed(ctx)
            return self._HandleResult(True)

        if frame_type == "asr.rearm.request":
            keep_warm_ms = self._policy_keep_warm_ms(ctx)
            _log.info(
                "evt=asr_prearm_requested sid=%s source=client keep_stream_warm_ms=%s",
                ctx.sid,
                keep_warm_ms,
            )
            # --- START FIX: Explicitly send HUD state to fix the UI badge ---
            self._emit_hud_state(ctx, "Listening")
            # --- END FIX ---
            try:
                # Use the standard ASR readiness path so we never emit asr.ready
                # without a corresponding, fully-open ASR stream.
                await self._ensure_asr_ready(send, ctx, "asr_rearm_request")
            except Exception:  # pragma: no cover - defensive logging
                _log.exception("evt=ws_asr_ready_bundle_failed sid=%s", ctx.sid)
            return self._HandleResult(True)

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
                if vendor_value == "gcp" and stage_value in {"ready", "started", "closed"}:
                    bus.publish(
                        {
                            "type": EVT_WS_JSON_RECV_SUMMARY,
                            "sid": ctx.sid,
                            "who": "server",
                            "source": "ws.adapter",
                            "meta": {
                                "kind": stage_value,
                                "vendor": "gcp",
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
                await self._publish_json_recv(ctx, meta, frame_payload)
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
                await self._publish_json_recv(ctx, meta, frame_payload)
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
                await self._publish_json_recv(ctx, meta, frame_payload)
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
                await self._publish_json_recv(ctx, meta, frame_payload)
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
                await self._publish_json_recv(ctx, meta, frame_payload)
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
                await self._publish_json_recv(ctx, meta, frame_payload)
                await self._send_error(
                    send,
                    ctx.sid,
                    "schema_invalid",
                    "client.telemetry meta must be an object if provided",
                )
                return self._HandleResult(True)

            if isinstance(event_name, str):
                self._handle_client_telemetry(ctx, event_name, telemetry_meta_dict)
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

        await self._publish_json_recv(ctx, meta, frame_payload)
        await self._invoke_engine("on_json", ctx.sid, frame)
        return self._HandleResult(True)

    async def _handle_client_turn_stop(
        self,
        ctx: AdapterContext,
        *,
        reason: str,
        frame: Mapping[str, Any] | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        reason_label = reason or "client_turn_stop"
        ctx.client_turn_closed = True
        ctx.accepting_audio = False
        ctx.audio_violation_count = 0
        ctx.partial_coalescer.cancel()

        try:
            await self._publish(
                EVT_WS_JSON_RECV,
                ctx.sid,
                meta if isinstance(meta, Mapping) else {"type": "input.stop", "ok": True},
                frame,
            )
        except Exception:
            _log.exception("evt=client_turn_stop_publish_failed sid=%s", ctx.sid)

        frame_ts = None
        try:
            frame_ts = frame.get("ts") if isinstance(frame, Mapping) else None
        except Exception:
            frame_ts = None

        _log.info(
            "evt=client_turn_stop sid=%s reason=%s frame_ts=%s", ctx.sid, reason_label, frame_ts
        )

        if not ctx.asr_open:
            return

        try:
            if not ctx.asr_final_emitted and isinstance(ctx.last_asr_partial, str) and ctx.last_asr_partial.strip():
                await self._handle_asr_result(ctx, ctx.last_asr_partial, True)
                return
        except Exception:
            _log.exception("evt=client_turn_stop_force_final_failed sid=%s", ctx.sid)

        try:
            await self._close_asr(ctx, reason=reason_label)
        except Exception:
            _log.exception("evt=client_turn_stop_close_failed sid=%s", ctx.sid)

    async def _handle_binary(
        self, data: bytes, ctx: AdapterContext, send: Callable[[dict], Awaitable[None]]
    ) -> _HandleResult:
        byte_count = len(data)
        ctx.last_client_activity_ms = int(time.time() * 1000)
        self._cancel_no_audio_watchdog(ctx)

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

        if (
            getattr(ctx, "control_codec", "json") == "msgpack"
            and msgpack is not None
            and byte_count <= self.text_limit_bytes
        ):
            try:
                frame_obj = msgpack.unpackb(data, raw=False)
            except Exception:
                frame_obj = None
            if isinstance(frame_obj, Mapping) and isinstance(frame_obj.get("type"), str):
                json_payload = json.dumps(frame_obj, separators=(",", ":"))
                return await self._handle_text(
                    json_payload,
                    ctx,
                    send,
                    codec="msgpack",
                    raw_bytes=data,
                    predecoded=frame_obj,
                )

        if ctx.client_turn_closed:
            await self._publish(
                EVT_WS_AUDIO_RECV,
                ctx.sid,
                {"byte_count": byte_count, "error": "audio_after_turn_stop"},
            )
            _log.debug("evt=audio_after_turn_stop_ignored sid=%s", ctx.sid)
            return self._HandleResult(True)

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

        if ctx.session.asr_state != "open":
            drop_meta = {
                "reason": "not_open",
                "byte_count": byte_count,
                "state": ctx.session.asr_state,
            }
            if isinstance(ctx.session.closed_at_ms, int):
                drop_meta["after_close_ms"] = max(
                    0, self._now_ms() - ctx.session.closed_at_ms
                )
            await self._publish(ASR_POST_CLOSE_DROP, ctx.sid, drop_meta)
            return self._HandleResult(True)

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
                    "type": WS_AUDIO_FIRST_CHUNK,
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
            ctx.asr_vendor == "gcp"
            and ctx.audio_chunks_recv == 0
            and data.startswith(b"\x1A\x45\xDF\xA3")
        ):
            meta = {
                "byte_count": byte_count,
                "error": "unexpected_container",
                "ws": {"dir": "in", "size": byte_count},
            }
            await self._publish(EVT_WS_AUDIO_RECV, ctx.sid, meta)
            detail = "gcp streaming requires raw pcm audio"
            await self._send_error(send, ctx.sid, "audio_container_mismatch", detail)
            self._emit_session_step(
                ctx.sid,
                "audio.stream_closed",
                summary="Closed audio stream due to unexpected container",
                meta={"reason": "unexpected_container", "vendor": "gcp"},
                source="ws.audio",
            )
            _log.error(
                "asr_stream_closed reason=unexpected_container vendor=gcp sid=%s",
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

        ctx.ing_chunks += 1
        ctx.ing_bytes += byte_count
        channels = 1
        profile = ctx.audio_profile
        if isinstance(profile, Mapping):
            channel_value = profile.get("channels")
            if isinstance(channel_value, int) and channel_value > 0:
                channels = channel_value
        bytes_per_frame = max(1, 2 * channels)
        ctx.ing_frames += byte_count // bytes_per_frame
        self._ensure_ingress_tick_timer(ctx)

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
        now_ms = self._now_ms()
        self._maybe_update_server_vad(ctx, chunk, now_ms)
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

        mic_lane_force_open = ctx.asr_open
        gate_open = mic_lane_force_open or (
            mask_open and ready_gate and mic_open and send_open and backpressure_ok
        )
        if mic_lane_force_open and not (
            mask_open and ready_gate and mic_open and send_open and backpressure_ok
        ):
            _log.debug("evt=audio_gate_bypass sid=%s reason=asr_open", ctx.sid)

        if not gate_open:
            _log.info(
                "evt=audio_gate_block sid=%s mask=%s ready=%s mic=%s send=%s back=%s",
                ctx.sid,
                mask_open,
                ready_gate,
                mic_open,
                send_open,
                backpressure_ok,
            )

        if gate_open:
            await self._flush_audio_backlog(ctx)
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
            await self._forward_audio_chunk(ctx, chunk, seq)
            return

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

        sp = self._server_policy(ctx)
        try:
            queue_pre = bool(sp.get("queue_pre_ready_audio", True))
        except Exception:
            queue_pre = True
        if reason in ("not_ready", "send_closed", "backpressure") and queue_pre:
            self._queue_audio_backlog(ctx, chunk, seq)
            _log.info(
                "evt=audio_backlog_enqueue sid=%s reason=%s pending=%d bytes=%d",
                ctx.sid,
                reason,
                len(ctx.audio_backlog),
                ctx.audio_backlog_bytes,
            )
            return
        _log.info(
            "evt=audio_drop sid=%s len=%d reason=%s",
            ctx.sid,
            byte_count,
            reason,
        )
        return

    def _queue_audio_backlog(self, ctx: AdapterContext, chunk: bytes, seq: int) -> None:
        backlog = ctx.audio_backlog
        backlog.append((seq, bytes(chunk)))
        ctx.audio_backlog_bytes += len(chunk)
        _log.info(
            "evt=audio_backlog_queue sid=%s len=%d total_bytes=%d pending=%d",
            ctx.sid,
            len(chunk),
            ctx.audio_backlog_bytes,
            len(backlog),
        )
        while ctx.audio_backlog_bytes > self.THROTTLE_RING_BUFFER_MAX_BYTES and backlog:
            _seq, drop_chunk = backlog.popleft()
            ctx.audio_backlog_bytes -= len(drop_chunk)
            _log.info(
                "evt=audio_backlog_trim sid=%s len=%d total_bytes=%d pending=%d",
                ctx.sid,
                len(drop_chunk),
                ctx.audio_backlog_bytes,
                len(backlog),
            )

    async def _flush_audio_backlog(self, ctx: AdapterContext) -> None:
        if not ctx.audio_backlog:
            return
        while ctx.audio_backlog:
            seq, queued = ctx.audio_backlog.popleft()
            ctx.audio_backlog_bytes -= len(queued)
            _log.info(
                "evt=audio_backlog_flush sid=%s len=%d remaining=%d",
                ctx.sid,
                len(queued),
                len(ctx.audio_backlog),
            )
            await self._forward_audio_chunk(ctx, queued, seq)

    def _is_keepalive_chunk(self, chunk: bytes, profile: Optional[Mapping[str, Any]]) -> bool:
        if not chunk:
            return False
        sample_rate = 16000
        channels = 1
        if isinstance(profile, Mapping):
            try:
                rate_value = int(profile.get("sample_rate")) if profile.get("sample_rate") is not None else None
            except Exception:
                rate_value = None
            if isinstance(rate_value, int) and rate_value > 0:
                sample_rate = rate_value
            try:
                channels_value = int(profile.get("channels")) if profile.get("channels") is not None else None
            except Exception:
                channels_value = None
            if isinstance(channels_value, int) and channels_value > 0:
                channels = channels_value
        expected_samples = int(round(sample_rate * (AUDIO_KEEPALIVE_CHUNK_MS / 1000.0))) * channels
        expected_bytes = expected_samples * PCM_BYTES_PER_SAMPLE
        if expected_bytes <= 0 or len(chunk) != expected_bytes:
            return False
        if isinstance(chunk, memoryview):
            chunk_view = chunk
        else:
            chunk_view = memoryview(chunk)
        return not any(chunk_view)

    async def _forward_audio_chunk(
        self,
        ctx: AdapterContext,
        chunk: bytes,
        seq: int,
        frame_meta: Optional[Mapping[str, Any]] = None,
    ) -> None:
        byte_count = len(chunk)
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
        keepalive = bool(frame_meta.get("keepalive")) if isinstance(frame_meta, Mapping) else False
        if not keepalive:
            keepalive = self._is_keepalive_chunk(chunk, ctx.audio_profile)
        if keepalive and ctx.session.asr_state == "open":
            try:
                await self._publish(
                    ASR_KEEPALIVE_PING,
                    ctx.sid,
                    {"bytes": byte_count, "engine": ctx.asr_vendor or "gcp"},
                )
            except Exception:  # pragma: no cover - defensive logging
                _log.exception("evt=asr_keepalive_telemetry_failed sid=%s", ctx.sid)
        engine = ctx.session.asr_engine
        if ctx.session.asr_state == "open" and engine is not None:
            try:
                await engine.write(chunk)
            except Exception:
                _log.warning("evt=asr_pcm_send_failed sid=%s", ctx.sid, exc_info=True)
            else:
                ctx.asr_bytes_sent += byte_count
                if not ctx.session.first_chunk_sent:
                    ctx.session.first_chunk_sent = True
                now_mono = time.monotonic()
                first_packet = ctx.asr_first_packet_monotonic
                if not isinstance(first_packet, (int, float)):
                    first_packet = now_mono
                    ctx.asr_first_packet_monotonic = first_packet
                if not ctx.asr_first_packet_logged:
                    try:
                        self._bus(
                            "asr.first_packet",
                            {"sid": ctx.sid, "bytes": byte_count},
                        )
                    except Exception:
                        pass
                    else:
                        ctx.asr_first_packet_logged = True
                ctx.asr_opened_ms = ctx.asr_opened_ms or self._now_ms()
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

    def _log_asr_control_summary(
        self, sid: str, payload: Mapping[str, Any]
    ) -> None:
        if not isinstance(payload, Mapping):
            return
        vendor = payload.get("vendor")
        if not (isinstance(vendor, str) and vendor.strip().lower() == "gcp"):
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
                "evt=ws_json_send_summary sid=%s kind=start vendor=gcp format=%s rate=%d channels=%d",
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
                        "vendor": "gcp",
                        "format": normalized_format,
                        "rate": normalized_rate,
                        "channels": normalized_channels,
                    },
                }
            )
        elif command == "stop":
            _log.info(
                "evt=ws_json_send_summary sid=%s kind=stop vendor=gcp",
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
                        "vendor": "gcp",
                    },
                }
            )
        elif command in {"ping", "pong"}:
            _log.info(
                "evt=ws_json_send_summary sid=%s kind=%s vendor=gcp",
                sid,
                command,
            )

    async def _send_json(
        self,
        send: Callable[[dict], Awaitable[None]],
        sid: str,
        payload: Dict[str, Any],
        *,
        ctx: AdapterContext | None = None,
    ) -> None:
        context = ctx or self._contexts.get(sid)
        codec = "json"
        if context is not None:
            codec = getattr(context, "control_codec", "json")
        if codec == "msgpack" and msgpack is None:
            _log.warning("evt=ws_send_codec_fallback sid=%s reason=msgpack_unavailable", sid)
            codec = "json"

        frame_payload: Dict[str, Any] = dict(payload)

        if codec == "msgpack" and msgpack is not None:
            payload_bytes = msgpack.packb(payload, use_bin_type=True)
            byte_count = len(payload_bytes)
            ws_meta: Dict[str, Any] = {"dir": "out", "size": byte_count, "codec": "msgpack"}
            meta: Dict[str, Any] = {
                "byte_count": byte_count,
                "frame_type": payload.get("type"),
                "ws": ws_meta,
            }
            ws_meta["from_adapter"] = True
            preview = None
            try:
                preview_bytes = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            except Exception:
                preview_bytes = None
            else:
                preview = self._make_preview_from_bytes(preview_bytes)
            if preview is not None:
                ws_meta["preview"] = preview
            try:
                await send({"type": "websocket.send", "bytes": payload_bytes})
            except RuntimeError as e:
                if "websocket.close" in str(e) or "response already completed" in str(e):
                    _log.warning(
                        "evt=ws_send_skipped sid=%s reason=asgi_closed type=%s",
                        sid,
                        payload.get("type"),
                    )
                    ws_meta["send_skipped"] = True
                    ws_meta["skipped_reason"] = "asgi_closed"
                    self._log_asr_control_summary(sid, payload)
                    await self._publish(EVT_WS_JSON_SEND, sid, meta, frame_payload)
                    return
                raise

            self._log_asr_control_summary(sid, payload)
            await self._publish(EVT_WS_JSON_SEND, sid, meta, frame_payload)
            return

        text_payload = json.dumps(payload, separators=(",", ":"))
        payload_bytes = text_payload.encode("utf-8")
        byte_count = len(payload_bytes)
        ws_meta = {"dir": "out", "size": byte_count, "codec": "json"}
        meta = {
            "byte_count": byte_count,
            "frame_type": payload.get("type"),
            "ws": ws_meta,
        }
        ws_meta["from_adapter"] = True
        preview = self._make_preview_from_bytes(payload_bytes)
        if preview is not None:
            ws_meta["preview"] = preview
        try:
            parsed_frame = json.loads(text_payload)
        except json.JSONDecodeError:
            parsed_frame = None
        if isinstance(parsed_frame, Mapping):
            frame_payload = dict(parsed_frame)
        try:
            await send({"type": "websocket.send", "text": text_payload})
        except RuntimeError as e:
            if "websocket.close" in str(e) or "response already completed" in str(e):
                _log.warning(
                    "evt=ws_send_skipped sid=%s reason=asgi_closed type=%s",
                    sid,
                    payload.get("type"),
                )
                ws_meta["send_skipped"] = True
                ws_meta["skipped_reason"] = "asgi_closed"
                self._log_asr_control_summary(sid, payload)
                await self._publish(EVT_WS_JSON_SEND, sid, meta, frame_payload)
                return
            raise

        self._log_asr_control_summary(sid, payload)
        await self._publish(EVT_WS_JSON_SEND, sid, meta, frame_payload)


    def _log_event(self, level: str, evt: str, sid: str, **kwargs: Any) -> None:
        log_fn = getattr(_log, level, _log.info)
        suffix = " ".join(f"{key}=%s" for key in kwargs)
        if suffix:
            log_fn(f"evt={evt} sid=%s {suffix}", sid, *kwargs.values())
        else:
            log_fn("evt=%s sid=%s", evt, sid)

    async def _publish_event(
        self,
        event_name: str,
        ctx: AdapterContext,
        meta: Mapping[str, Any] | None,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        meta_payload = dict(meta) if isinstance(meta, Mapping) else {}
        await self._publish(event_name, ctx.sid, meta_payload, payload)

    async def _publish_json_recv(
        self,
        ctx: AdapterContext,
        meta: Mapping[str, Any],
        frame_payload: Mapping[str, Any] | None,
    ) -> None:
        await self._publish_event(EVT_WS_JSON_RECV, ctx, meta, frame_payload)

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

    async def _send_asr_error(
        self, send: Callable[[dict], Awaitable[None]], ctx: AdapterContext, code: str
    ) -> None:
        await self._send_json(send, ctx.sid, {"type": "asr.error", "code": code})

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
                now_ms = int(time.time() * 1000)
                ctx.last_server_ping_ms = now_ms
                await self._send_json(send, ctx.sid, {"type": "server.ping", "ts": now_ms})
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
        sp = self._server_policy(ctx)
        try:
            deadline_ms = int(sp.get("asr_ready_deadline_ms", 8000))
        except Exception:
            deadline_ms = 8000
        if deadline_ms > 0:
            deadline_task = ctx.asr_ready_deadline_task
            if deadline_task is not None:
                deadline_task.cancel()
            ctx.asr_ready_deadline_task = asyncio.create_task(
                self._arm_asr_ready_deadline(send, ctx, deadline_ms)
            )

        def _schedule_throttle(reason: str) -> None:
            try:
                loop.create_task(self._maybe_emit_audio_throttle(send, ctx, reason=reason))
            except RuntimeError:
                try:
                    asyncio.create_task(self._maybe_emit_audio_throttle(send, ctx, reason=reason))
                except RuntimeError:
                    pass

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
                _schedule_throttle("outbox_full")
                return
            now = ctx.outbox.qsize()
            if now >= _AUDIO_THROTTLE_QUEUE_THRESHOLD:
                _schedule_throttle("outbox_depth")

        def _enqueue(payload: Dict[str, Any]) -> None:
            frame_type = payload.get("type")
            if frame_type == "input.start":
                self._mark_input_start(ctx)
                ctx.client_mic_open = True
            elif frame_type == "turn.end":
                if ctx.turn_active:
                    ctx.turn_active = False
                    ctx.client_mic_open = False
            elif frame_type == "turn.begin":
                if not ctx.turn_active:
                    ctx.turn_active = True
            elif frame_type == "asr.turn":
                state_value = payload.get("state") if isinstance(payload, Mapping) else None
                if state_value == "begin":
                    if not ctx.turn_active:
                        ctx.turn_active = True
                        _queue_payload({"type": "turn.begin"})
                elif state_value == "end":
                    if ctx.turn_active:
                        ctx.turn_active = False
                        ctx.client_mic_open = False
                        _queue_payload({"type": "turn.end"})
            if frame_type == "asr.partial":
                self._offer_partial_frame(
                    ctx,
                    loop,
                    payload,
                    lambda frame: _queue_payload(frame, clone=False),
                )
                return
            _queue_payload(payload)

        def _on_tts_event(event: dict) -> None:
            if event.get("sid") != ctx.sid:
                return
            event_type = event.get("type")
            if event_type == EVT_TTS_START:
                handler = self._handle_tts_start
            elif event_type == EVT_TTS_END:
                handler = self._handle_tts_end
            else:
                return

            async def _run() -> None:
                try:
                    await handler(send, ctx, event)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    _log.exception(
                        "evt=ws_tts_event_failed sid=%s event_type=%s", ctx.sid, event_type
                    )

            try:
                loop.call_soon_threadsafe(lambda: asyncio.create_task(_run()))
            except RuntimeError:
                asyncio.create_task(_run())

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
                    policy_mapping = (
                        ctx.policy_snapshot
                        if FEATURE_LEGACY_POLICY
                        and isinstance(ctx.policy_snapshot, Mapping)
                        else self._policy(ctx)
                    )
                    self._maybe_emit_await_user(ctx, policy_mapping)
                    _schedule_listen_handoff(req_value)
                    ctx.session.tts_active = False
                    try:
                        loop.create_task(self._handle_tts_end(send, ctx, payload))
                    except RuntimeError:
                        asyncio.create_task(self._handle_tts_end(send, ctx, payload))
                    if ctx.session.queued_arm and can_open(ctx.session):
                        try:
                            loop.create_task(
                                self._publish_event(
                                    ASR_OPEN_AFTER_TTS,
                                    ctx,
                                    {
                                        "reason": "tts_end",
                                        "state": ctx.session.asr_state,
                                    },
                                )
                            )
                        except RuntimeError:
                            pass
                elif frame_type == "tts.start":
                    ctx.session.tts_active = True
                    try:
                        loop.create_task(self._handle_tts_start(send, ctx, payload))
                    except RuntimeError:
                        asyncio.create_task(self._handle_tts_start(send, ctx, payload))
                _enqueue(payload)

            try:
                loop.call_soon_threadsafe(_on_loop)
            except RuntimeError:
                pass

        ctx.tts_bus_token_start = bus.subscribe(EVT_TTS_START, _on_tts_event)
        ctx.tts_bus_token_end = bus.subscribe(EVT_TTS_END, _on_tts_event)

        ctx.subscription_token = bus.subscribe(EVT_WS_JSON_SEND, _handle_event)

        def _handle_asr_event(event: dict) -> None:
            if event.get("sid") != ctx.sid or ctx.outbox is None:
                return

            event_type = event.get("type")
            if event_type in {EVT_ASR_PARTIAL, EVT_ASR_FINAL}:
                now_ms = self._now_ms()
                ctx.session.last_vendor_activity_ms = float(now_ms)
                if isinstance(event, Mapping):
                    self._emit_server_vendor_activity(ctx, event_type, event, now_ms)
                if event_type == EVT_ASR_PARTIAL:
                    self._maybe_emit_first_token_latency(ctx, "partial", now_ms)
                else:
                    self._maybe_emit_first_token_latency(ctx, "final", now_ms)

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

        def _handle_asr_closed_event(event: dict) -> None:
            if event.get("sid") != ctx.sid or ctx.outbox is None:
                return

            def _on_loop() -> None:
                turn_end_payload = self._prepare_asr_turn_end(ctx, "eos")
                if turn_end_payload is not None:
                    _enqueue(turn_end_payload)
                if ctx.asr_closed_ack_sent:
                    return
                ack_seq = uuid.uuid4().hex
                reason = ctx.asr_close_reason or "server_closed"
                # Maintain ordering: final transcripts (if any) precede the closed ACK,
                # which in turn precedes downstream turn / chat / TTS events.
                ack_frame = self._build_asr_closed_ack(
                    ctx,
                    seq=ack_seq,
                    sid=self._current_asr_sid(ctx),
                    reason=reason,
                    status="server_closed",
                )
                _enqueue(ack_frame)
                ctx.asr_closed_ack_sent = True
                _log.info(
                    "evt=asr_close_ack sid=%s seq=%s status=%s final_emitted=%s bytes=%d",
                    ctx.sid,
                    ack_seq,
                    "server_closed",
                    ctx.asr_final_emitted,
                    ctx.asr_bytes_sent,
                )

            try:
                loop.call_soon_threadsafe(_on_loop)
            except RuntimeError:
                pass

        ctx.asr_closed_subscription_token = bus.subscribe(
            EVT_ASR_CLOSED, _handle_asr_closed_event
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
                ctx.session.eot_armed = False
                ctx.session.server_vad_speech = False
                ctx.session.server_vad_since_ms = None
                ctx.pending_start_listening = None
                ctx.pending_start_listening_sent = False
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
                if ctx.asr_vendor == "gcp":
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
            if ctx.tts_mask_phase != "off":
                self._set_after_mask_for_key(ctx, key)
                self._clear_pending_for_key(ctx, key)
                _log.info(
                    "evt=listen_handoff_aborted reason=mask_on sid=%s req_id=%s",
                    ctx.sid,
                    req_id,
                )
                return

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
            ready_frame["sid"] = ctx.sid
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
            ctx.pending_start_listening = dict(start_payload)
            ctx.pending_start_listening_sent = True
            _enqueue(start_payload)

            turn_begin_payload = self._prepare_asr_turn_begin(ctx, "ready_bundle")
            if turn_begin_payload is not None:
                _enqueue(turn_begin_payload)

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
                policy_mapping = (
                    ctx.policy_snapshot
                    if FEATURE_LEGACY_POLICY
                    and isinstance(ctx.policy_snapshot, Mapping)
                    else self._policy(ctx)
                )
                self._maybe_emit_await_user(ctx, policy_mapping)
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
                    policy_mapping = (
                        ctx.policy_snapshot
                        if FEATURE_LEGACY_POLICY
                        and isinstance(ctx.policy_snapshot, Mapping)
                        else self._policy(ctx)
                    )
                    self._maybe_emit_await_user(ctx, policy_mapping)

            try:
                loop.call_soon_threadsafe(_on_loop)
            except RuntimeError:
                _on_loop()

        ctx.turn_state_subscription_token = bus.subscribe(
            "EVT_TURN_STATE", _handle_turn_state_event
        )

        def _handle_audio_event(event: dict) -> None:
            source = event.get("source") or "unknown"

            def _log_ignored(reason: str) -> None:
                _log.debug(
                    "evt=audio_event_ignored sid=%s source=%s reason=%s",
                    ctx.sid,
                    source,
                    reason,
                )

            if event.get("sid") != ctx.sid:
                _log_ignored("sid_mismatch")
                return
            if ctx.audio_send_closed:
                _log_ignored("audio_send_closed")
                return
            if source == "ws_server":
                meta = event.get("meta")
                audio_meta = meta.get("audio") if isinstance(meta, Mapping) else None
                if not isinstance(audio_meta, Mapping):
                    _log_ignored("missing_audio_meta")
                    return
            chunk = event.get("chunk")
            if isinstance(chunk, (bytes, bytearray, memoryview)):
                chunk_bytes = bytes(chunk)
            else:
                _log_ignored("chunk_not_bytes")
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

                if len(ctx.audio_tasks) >= _AUDIO_THROTTLE_AUDIO_TASK_THRESHOLD:
                    _schedule_throttle("audio_backlog")
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
            is_final = False
        elif event_type == EVT_ASR_FINAL:
            frame_type = "asr.final"
            is_final = True
        else:
            return None

        raw_meta = event.get("meta")
        meta = raw_meta if isinstance(raw_meta, Mapping) else None
        text = event.get("text")

        no_speech = bool(meta and meta.get("no_speech"))

        # Try fallback text from meta if needed
        if not isinstance(text, str) or not text.strip():
            if meta is not None:
                candidate_text = meta.get("text")
                if isinstance(candidate_text, str):
                    text = candidate_text

        # Drop truly empty/noise finals, but honor explicit no-speech signals.
        if not isinstance(text, str) or not text.strip():
            # Vendor explicitly signaled silence: end input cleanly so the UI resets.
            if no_speech:
                return {"type": "input.stop", "reason": "no_speech"}
            
            # --- START FIX B3: Cleanly close mic on empty final to prevent hang ---
            if frame_type == "asr.final":
                return {"type": "input.stop", "reason": "empty_transcript"}
            # --- END FIX B3 ---
            
            return None

        frame: Dict[str, Any] = {"type": frame_type, "text": text}
        frame["sid"] = ctx.sid

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
            frame["vendor"] = vendor

        meta_stream = meta.get("stream_id") if isinstance(meta, dict) else None
        if isinstance(meta_stream, str) and meta_stream:
            frame["stream_id"] = meta_stream

        if meta is not None:
            partial_seq = meta.get("partial_seq")
            if isinstance(partial_seq, int):
                frame["partial_seq"] = partial_seq

        if frame_type == "asr.partial":
            ctx.last_asr_partial = text
        elif frame_type == "asr.final":
            ctx.last_asr_partial = None

        return frame

    def _current_asr_sid(self, ctx: AdapterContext) -> str:
        engine = ctx.session.asr_engine
        if engine is not None:
            sid = getattr(engine, "sid", None) or getattr(engine, "_sid", None)
            if isinstance(sid, str) and sid:
                return sid
        return ctx.sid

    def _estimate_ms_ingested(self, ctx: AdapterContext, byte_count: int) -> int:
        if byte_count <= 0:
            return 0
        profile: Optional[Mapping[str, Any]] = None
        session_profile = getattr(ctx.session, "audio_profile", None)
        if isinstance(session_profile, Mapping):
            profile = session_profile
        elif isinstance(ctx.audio_profile, Mapping):
            profile = ctx.audio_profile

        sample_rate = _DEFAULT_GCP_SAMPLE_RATE_HZ
        channels = 1
        if profile is not None:
            raw_rate = profile.get("sample_rate") or profile.get("rate_hz")
            try:
                parsed_rate = int(raw_rate)
            except (TypeError, ValueError):
                parsed_rate = sample_rate
            if parsed_rate > 0:
                sample_rate = parsed_rate
            raw_channels = profile.get("channels")
            try:
                parsed_channels = int(raw_channels)
            except (TypeError, ValueError):
                parsed_channels = channels
            if parsed_channels > 0:
                channels = parsed_channels

        bytes_per_sample = max(1, channels) * 2
        if sample_rate <= 0 or bytes_per_sample <= 0:
            return 0
        samples = byte_count / bytes_per_sample
        ms = (samples / sample_rate) * 1000.0
        return int(round(ms))

    def _build_asr_closed_ack(
        self,
        ctx: AdapterContext,
        *,
        seq: str,
        sid: str,
        reason: str,
        status: str,
    ) -> Dict[str, Any]:
        bytes_ingested = max(0, ctx.asr_bytes_sent)
        ms_ingested = self._estimate_ms_ingested(ctx, bytes_ingested)
        return {
            "type": "asr.closed",
            "seq": seq,
            "sid": sid,
            "reason": reason,
            "status": status,
            "final_emitted": bool(ctx.asr_final_emitted),
            "bytes_ingested": bytes_ingested,
            "ms_ingested": ms_ingested,
        }

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
            if ctx.session.asr_state != "open":
                mark(ctx.session, "open")
            ctx.awaiting_asr_ready = False
            ctx.asr_recovering_until = 0.0
            ctx.asr_recovering_reason = None
            ctx.asr_recovering_audio_logged = False
            ctx.client_capture_armed = True
            now_ms = self._now_ms()
            ctx.session.eot_armed = True
            ctx.session.last_pcm_ms = float(now_ms)
            ctx.session.last_vendor_activity_ms = float(now_ms)
            ctx.session.server_vad_speech = False
            ctx.session.server_vad_since_ms = float(now_ms)
            ctx.server_vad_candidate_start_ms = None
            ctx.server_vad_silence_candidate_ms = None
            ctx.server_vad_energy_db = _DB_FLOOR
            ctx.client_vad_speech = False
            ctx.client_vad_since_ms = now_ms
            policy = self._resolve_server_vad_policy(ctx)
            if policy.get("enable", True):
                threshold = float(
                    policy.get("energy_threshold_dbfs", _SERVER_VAD_DEFAULT_POLICY["energy_threshold_dbfs"])
                )
                state_meta = {
                    "speech": False,
                    "energy_db": _DB_FLOOR,
                    "threshold_db": threshold,
                    "since_ms": now_ms,
                }
                self._publish_server_vad_event(ctx, "server.vad.state", state_meta)
            self._ensure_vad_fusion_task(ctx)

            frame: Dict[str, Any] = {"type": "asr.ready", "sid": ctx.sid}
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
                        "frame": frame,
                        "payload": frame,
                    }
                )
                self._publish_pending_start_listening(ctx, telemetry_bus)

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
                if FEATURE_LEGACY_POLICY:
                    ctx.policy_snapshot = policy_payload
                else:
                    ctx.policy_snapshot = None
                self._replace_policy(ctx, policy_payload)
                ctx.allowed_asr_vendors = ["gcp"]
                if FEATURE_LEGACY_POLICY:
                    snapshot_mapping = policy_payload
                else:
                    snapshot_mapping = (
                        ctx.policy if isinstance(ctx.policy, Mapping) else None
                    )
                ctx.asr_vendor = "gcp"
                if not ctx.asr_vendor_logged:
                    allowed_display = ",".join(ctx.allowed_asr_vendors)
                    _log.info(
                        "asr_vendor_selected primary=%s allowed=%s reason=%s",
                        ctx.asr_vendor,
                        allowed_display,
                        "pcm16_only",
                    )
                    ctx.asr_vendor_logged = True
                ctx.audio_pipeline_mode = self._resolve_audio_pipeline_mode(
                    snapshot_mapping
                )
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
            and ctx.asr_vendor.lower() == "gcp"
        ):
            return
        lock = self._ensure_send_lock(ctx)
        async with lock:
            frame_type = payload.get("type")
            if frame_type in {"asr.partial", "asr.final"}:
                vendor = payload.get("vendor")
                if isinstance(vendor, str) and vendor.lower() == "gcp":
                    log_event = (
                        "evt=asr_partial_sent"
                        if frame_type == "asr.partial"
                        else "evt=asr_final_sent"
                    )
                    text_value = payload.get("text")
                    text_len = len(text_value) if isinstance(text_value, str) else 0
                    _log.info("%s vendor=gcp chars=%d", log_event, text_len)
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
        self._cancel_no_audio_watchdog(ctx)
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

        closed_token = ctx.asr_closed_subscription_token
        ctx.asr_closed_subscription_token = None
        if closed_token:
            bus.unsubscribe(closed_token)

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

        tts_bus_start = ctx.tts_bus_token_start
        ctx.tts_bus_token_start = None
        if tts_bus_start:
            bus.unsubscribe(tts_bus_start)

        tts_bus_end = ctx.tts_bus_token_end
        ctx.tts_bus_token_end = None
        if tts_bus_end:
            bus.unsubscribe(tts_bus_end)

        task = ctx.outbound_task
        ctx.outbound_task = None
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        ctx.partial_coalescer.cancel()

        fusion_task = ctx.vad_fusion_task
        ctx.vad_fusion_task = None
        if fusion_task is not None:
            fusion_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await fusion_task

        pending_handoff = ctx.listen_handoff_task
        ctx.listen_handoff_task = None
        ctx.listen_handoff_task_key = None
        if pending_handoff is not None:
            pending_handoff.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pending_handoff

        deadline_task = ctx.asr_ready_deadline_task
        ctx.asr_ready_deadline_task = None
        if deadline_task is not None:
            deadline_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await deadline_task

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
        ctx.await_user_vad_check_pending = False
        self._policy_defaults_emitted.pop(ctx.sid, None)

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

    def _arm_no_audio_watchdog(self, ctx: AdapterContext) -> None:
        self._cancel_no_audio_watchdog(ctx)
        ctx.no_audio_watchdog_t0_ms = self._now_ms()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            ctx.no_audio_timer = None
            return

        try:
            ctx.no_audio_timer = loop.call_later(1.5, self._on_no_audio_after_header, ctx)
        except RuntimeError:
            ctx.no_audio_timer = None

    def _schedule_no_audio_watchdog_rearm(
        self, ctx: AdapterContext, delay_ms: int = 350
    ) -> None:
        if ctx.asr_ready_bundle_sent_ms is None:
            return
        handle = ctx.no_audio_rearm_handle
        if handle is not None:
            try:
                handle.cancel()
            except Exception:
                pass
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            ctx.no_audio_rearm_handle = None
            return

        delay_seconds = max(0.0, float(delay_ms) / 1000.0)

        def _rearm() -> None:
            ctx.no_audio_rearm_handle = None
            self._arm_no_audio_watchdog(ctx)

        try:
            ctx.no_audio_rearm_handle = loop.call_later(delay_seconds, _rearm)
        except RuntimeError:
            ctx.no_audio_rearm_handle = None

    def _cancel_no_audio_watchdog(self, ctx: AdapterContext) -> None:
        timer = ctx.no_audio_timer
        ctx.no_audio_timer = None
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass
        rearm = ctx.no_audio_rearm_handle
        ctx.no_audio_rearm_handle = None
        if rearm is not None:
            try:
                rearm.cancel()
            except Exception:
                pass
        ctx.no_audio_watchdog_t0_ms = None

    def _on_no_audio_after_header(self, ctx: AdapterContext) -> None:
        ctx.no_audio_timer = None
        start_ms = ctx.no_audio_watchdog_t0_ms
        ctx.no_audio_watchdog_t0_ms = None
        if start_ms is None:
            return

        since_ms = max(0, self._now_ms() - start_ms)
        phase = self._resolve_watchdog_phase(ctx)
        turn_id = getattr(ctx.session, "turn_id", None)
        detail = {
            "session_id": ctx.sid,
            "turn_id": turn_id if isinstance(turn_id, str) and turn_id else None,
            "phase": phase,
            "since_ms": since_ms,
        }

        _log.warning(
            "evt=asr_no_audio_after_header sid=%s since_ms=%d phase=%s",
            ctx.sid,
            since_ms,
            phase or "",
        )
        self._emit_hub_watchdog_log(ctx, detail)

    def _ensure_ingress_tick_timer(self, ctx: AdapterContext) -> None:
        if ctx.ing_last_tick_t0_ms is None:
            ctx.ing_last_tick_t0_ms = self._now_ms()
        if ctx.ing_tick_task is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        try:
            ctx.ing_tick_task = loop.call_later(2.0, self._on_ingress_tick_timer, ctx)
        except Exception:
            ctx.ing_tick_task = None
            _log.exception("evt=audio_ingress_tick_schedule_failed sid=%s", ctx.sid)

    def _cancel_ingress_tick_timer(self, ctx: AdapterContext) -> None:
        handle = ctx.ing_tick_task
        ctx.ing_tick_task = None
        if handle is not None:
            try:
                handle.cancel()
            except Exception:
                pass

    def _on_ingress_tick_timer(self, ctx: AdapterContext) -> None:
        ctx.ing_tick_task = None
        try:
            self._emit_ingress_tick(ctx)
        except Exception:
            _log.exception("evt=audio_ingress_tick_emit_failed sid=%s", ctx.sid)
        self._ensure_ingress_tick_timer(ctx)

    def _emit_ingress_tick(self, ctx: AdapterContext) -> None:
        now_ms = self._now_ms()
        last_ms = ctx.ing_last_tick_t0_ms or now_ms
        elapsed_ms = max(1, now_ms - last_ms)
        frames = ctx.ing_frames
        byte_count = ctx.ing_bytes
        chunk_count = ctx.ing_chunks
        avg_ms_per_chunk = int(elapsed_ms / max(1, chunk_count))
        backpressure = ctx.backpressure_state == "on"
        turn_id = getattr(ctx.session, "turn_id", None)
        detail = {
            "session_id": ctx.sid,
            "turn_id": turn_id if isinstance(turn_id, str) and turn_id else None,
            "frames": frames,
            "bytes": byte_count,
            "chunks": chunk_count,
            "avg_ms_per_chunk": avg_ms_per_chunk,
            "backpressure": backpressure,
        }
        try:
            self._emit_hub_log(ctx, "audio.ingress.tick", detail)
        finally:
            ctx.ing_frames = 0
            ctx.ing_bytes = 0
            ctx.ing_chunks = 0
            ctx.ing_last_tick_t0_ms = now_ms

    def _flush_ingress_tick(self, ctx: AdapterContext) -> None:
        self._cancel_ingress_tick_timer(ctx)
        if ctx.ing_last_tick_t0_ms is None:
            return
        if ctx.ing_frames == 0 and ctx.ing_bytes == 0 and ctx.ing_chunks == 0:
            return
        try:
            self._emit_ingress_tick(ctx)
        except Exception:
            _log.exception("evt=audio_ingress_tick_emit_failed sid=%s", ctx.sid)

    @staticmethod
    def _resolve_watchdog_phase(ctx: AdapterContext) -> Optional[str]:
        candidates: List[Optional[str]] = [
            getattr(ctx.session, "turn_phase", None),
            getattr(ctx.session, "turn_state", None),
            ctx.hud_state,
        ]
        mask_phase = ctx.tts_mask_phase if isinstance(ctx.tts_mask_phase, str) else None
        if mask_phase and mask_phase != "off":
            candidates.append(mask_phase)
        candidates.append(ctx.session.asr_state)
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate
        return None

    def _emit_hub_watchdog_log(self, ctx: AdapterContext, detail: Mapping[str, Any]) -> None:
        try:
            self._emit_hub_log(ctx, "asr.no_audio_after_header", detail)
        except Exception:
            _log.exception("evt=asr_no_audio_watchdog_emit_failed sid=%s", ctx.sid)

    def _emit_hub_log(
        self,
        ctx: AdapterContext,
        label: str,
        detail: Mapping[str, Any],
    ) -> None:
        if isinstance(detail, Mapping):
            enriched_detail: Dict[str, Any] = dict(detail)
        else:
            enriched_detail = {"detail": detail}

        sid = enriched_detail.get("session_id") or ctx.sid
        turn_id = enriched_detail.get("turn_id")
        ctx_turn_id = getattr(ctx.session, "turn_id", None)
        if not isinstance(turn_id, str) or not turn_id:
            if isinstance(ctx_turn_id, str) and ctx_turn_id:
                turn_id = ctx_turn_id

        if turn_id != ctx.hub_log_last_turn:
            ctx.hub_log_last_turn = turn_id
            ctx.hub_log_seq = 0

        if "seq" not in enriched_detail:
            enriched_detail["seq"] = ctx.hub_log_seq
            ctx.hub_log_seq += 1

        enriched_detail.setdefault("session_id", sid)
        enriched_detail.setdefault("turn_id", turn_id)

        try:
            sanitized = bus.redact_payload(enriched_detail)
        except Exception:
            sanitized = enriched_detail
        payload = {
            "type": EVT_CLIENT_LOG,
            "sid": ctx.sid,
            "who": "server",
            "source": "ws.adapter",
            "meta": {
                "label": label,
                "detail": sanitized,
            },
        }
        bus.publish(payload)

    def _mark_input_start(self, ctx: AdapterContext) -> None:
        now_ms = self._now_ms()
        ctx.input_start_ms = now_ms
        ctx.first_partial_logged = False
        ctx.first_final_logged = False

    def _maybe_emit_first_token_latency(
        self, ctx: AdapterContext, kind: Literal["partial", "final"], now_ms: int
    ) -> None:
        start_ms = ctx.input_start_ms
        if start_ms is None:
            return
        elapsed_ms = max(0, now_ms - int(start_ms))
        turn_id = getattr(ctx.session, "turn_id", None)
        detail: Dict[str, Any] = {
            "session_id": ctx.sid,
            "turn_id": turn_id if isinstance(turn_id, str) and turn_id else None,
            "elapsed_ms": int(elapsed_ms),
        }
        if kind == "partial":
            if ctx.first_partial_logged:
                return
            ctx.first_partial_logged = True
            self._emit_hub_log(ctx, "asr.first_partial", detail)
            return
        if ctx.first_final_logged:
            return
        ctx.first_final_logged = True
        config = ctx.active_asr_config if isinstance(ctx.active_asr_config, Mapping) else None
        max_delay = None
        if config is not None:
            candidate = config.get("max_delay")
            if isinstance(candidate, (int, float)):
                max_delay = float(candidate)
        final_detail = dict(detail)
        if max_delay is not None:
            final_detail["max_delay_s"] = max_delay
        self._emit_hub_log(ctx, "asr.first_final", final_detail)

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

        # --- START PATCH: CRITICAL CONCURRENCY INVARIANT ---
        # If ASR stream is open, the mic is already armed and actively listening.
        # Do NOT re-arm or close based on the conversational turn state.
        if ctx.session.asr_state == "open":
            _log.info("evt=listen_handoff_skip reason=asr_stream_open sid=%s", ctx.sid)
            return False
        # --- END PATCH ---

        key = self._turn_key(ctx, req_id)
        self._set_pending_for_key(ctx, key)
        self._clear_after_mask_for_key(ctx, key)
        _log.info("evt=listen_handoff_pending sid=%s req_id=%s", ctx.sid, req_id)
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
        ctx.session.eot_armed = False
        ctx.session.server_vad_speech = False
        ctx.session.server_vad_since_ms = None
        ctx.pending_start_listening = None
        ctx.pending_start_listening_sent = False
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

    def _publish_session_step_meta(
        self,
        sid: Optional[str],
        meta: Mapping[str, Any] | None,
    ) -> None:
        if not isinstance(sid, str) or not sid:
            return
        if not isinstance(meta, Mapping):
            return
        step_value = meta.get("step")
        if not isinstance(step_value, str) or not step_value:
            return

        payload = dict(meta)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            loop.create_task(self._publish(EVT_SESSION_STEP, sid, payload))
            return

        fallback_event = {
            "schema_version": "1",
            "type": EVT_SESSION_STEP,
            "sid": sid,
            "who": "server",
            "source": "ws_server",
            "meta": payload,
        }
        try:
            bus.publish(fallback_event)
        except Exception:  # pragma: no cover - defensive fallback
            _log.exception("evt=session_step_publish_failed sid=%s step=%s", sid, step_value)

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
            # --- START FIX: Prevent aggressive client re-arm after initial greet ---
            ctx.await_user_expected = False
            ctx.await_user_req_id = None
            # --- END FIX ---
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

        policy_block = stable.get("policy")
        asr_block: Optional[Dict[str, Any]] = None
        if isinstance(policy_block, Mapping):
            candidate = policy_block.get("asr")
            if isinstance(candidate, Mapping):
                asr_block = dict(candidate)
                policy_block = dict(policy_block)
                policy_block["asr"] = asr_block
                stable["policy"] = policy_block

        vendor_primary: Optional[str] = None
        if asr_block:
            vendor_block = asr_block.get("vendor")
            if isinstance(vendor_block, Mapping):
                vendor_primary = str(vendor_block.get("primary") or "").strip().lower()

        audio_block = stable.get("audio")
        pipeline_block: Optional[Dict[str, Any]] = None
        if isinstance(audio_block, Mapping):
            pipeline_candidate = audio_block.get("pipeline")
            if isinstance(pipeline_candidate, Mapping):
                pipeline_block = dict(pipeline_candidate)
                audio_block = dict(audio_block)
                audio_block["pipeline"] = pipeline_block
                stable["audio"] = audio_block

        capture_candidate = stable.get("capture")
        capture_block = dict(capture_candidate) if isinstance(capture_candidate, Mapping) else None
        if capture_block is not None:
            stable["capture"] = capture_block

        media_candidate = stable.get("media")
        media_block = dict(media_candidate) if isinstance(media_candidate, Mapping) else None
        if media_block is not None:
            stable["media"] = media_block

        audio_mode = (pipeline_block.get("mode") if pipeline_block else None) or ""
        audio_mode = str(audio_mode).strip().lower()
        should_apply_defaults = (vendor_primary == "gcp" or audio_mode == "pcm16")

        if should_apply_defaults:
            if isinstance(media_block, dict) and "asr_input" in media_block:
                media_block["asr_input"] = "pcm_16k"

            if pipeline_block is not None and "mode" in pipeline_block:
                pipeline_block["mode"] = "pcm16"

            timeslice_value: Optional[int] = None
            if isinstance(capture_block, dict) and "timeslice_ms" in capture_block:
                capture_block["timeslice_ms"] = 50
                timeslice_value = 50
            elif isinstance(capture_block, dict):
                raw_timeslice = capture_block.get("timeslice_ms")
                try:
                    timeslice_value = int(raw_timeslice) if raw_timeslice is not None else None
                except (TypeError, ValueError):
                    timeslice_value = None
            else:
                timeslice_value = None

            if isinstance(capture_block, dict) and "asr_input" in capture_block:
                capture_block["asr_input"] = "pcm_16k"

            if isinstance(stable.get("allow_auto_vad"), bool):
                stable["allow_auto_vad"] = True

            if isinstance(policy_block, dict):
                vad_block = policy_block.get("vad")
                if isinstance(vad_block, Mapping) and "allow_auto_vad" in vad_block:
                    vad_mut = dict(vad_block)
                    vad_mut["allow_auto_vad"] = True
                    policy_block["vad"] = vad_mut
                stable["policy"] = policy_block

            ue_ms = None
            cs_ms = None
            min_seg_ms = None
            allow_word_finals: Optional[bool] = None

            if isinstance(asr_block, dict):
                if "utterance_end_ms" in asr_block:
                    asr_block["utterance_end_ms"] = 2100
                if "commit_silence_ms" in asr_block:
                    asr_block["commit_silence_ms"] = 1400
                if "min_segment_ms" in asr_block:
                    asr_block["min_segment_ms"] = 1200
                if "allow_word_finals" in asr_block:
                    asr_block["allow_word_finals"] = False
                    allow_word_finals = False

                try:
                    ue_ms = int(asr_block.get("utterance_end_ms"))
                except (TypeError, ValueError):
                    ue_ms = None
                try:
                    cs_ms = int(asr_block.get("commit_silence_ms"))
                except (TypeError, ValueError):
                    cs_ms = None
                try:
                    min_seg_ms = int(asr_block.get("min_segment_ms"))
                except (TypeError, ValueError):
                    min_seg_ms = None
                if allow_word_finals is None and "allow_word_finals" in asr_block:
                    allow_word_finals = bool(asr_block.get("allow_word_finals"))

            timeslice_for_log = timeslice_value
            if timeslice_for_log is None and isinstance(capture_block, dict):
                try:
                    timeslice_for_log = int(capture_block.get("timeslice_ms"))
                except (TypeError, ValueError):
                    timeslice_for_log = None

            sid_for_publish = current_sid.get(None)
            emission_key: Optional[str] = None
            if isinstance(sid_for_publish, str) and sid_for_publish:
                emission_key = sid_for_publish
            elif sid_for_publish is None:
                emission_key = None
            should_emit = True
            if emission_key in self._policy_defaults_emitted:
                should_emit = False
            else:
                self._policy_defaults_emitted[emission_key] = True

            if should_emit:
                _log.info(
                    "evt=policy_defaults ue_ms=%s cs_ms=%s min_seg_ms=%s timeslice_ms=%s allow_word_finals=%s",
                    ue_ms if ue_ms is not None else "unknown",
                    cs_ms if cs_ms is not None else "unknown",
                    min_seg_ms if min_seg_ms is not None else "unknown",
                    timeslice_for_log if timeslice_for_log is not None else "unknown",
                    str(allow_word_finals).lower() if allow_word_finals is not None else "unknown",
                )

                policy_meta: Dict[str, Any] = {"step": "policy_defaults"}
                if ue_ms is not None:
                    policy_meta["ue_ms"] = ue_ms
                if cs_ms is not None:
                    policy_meta["cs_ms"] = cs_ms
                if min_seg_ms is not None:
                    policy_meta["min_seg_ms"] = min_seg_ms
                if timeslice_for_log is not None:
                    policy_meta["timeslice_ms"] = timeslice_for_log
                if allow_word_finals is not None:
                    policy_meta["allow_word_finals"] = bool(allow_word_finals)
                self._publish_session_step_meta(sid_for_publish, policy_meta)
        return stable

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
        if FEATURE_LEGACY_POLICY and isinstance(ctx.policy_snapshot, Mapping):
            capture_block = ctx.policy_snapshot.get("capture")
            if isinstance(capture_block, Mapping):
                candidates.append(capture_block)
        else:
            policy_capture = (
                ctx.policy.get("capture")
                if isinstance(ctx.policy, Mapping)
                else None
            )
            if isinstance(policy_capture, Mapping):
                candidates.append(policy_capture)
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

    def _resolve_asr_config(self, ctx: AdapterContext) -> Dict[str, Any]:
        default_language = getattr(config, "GCP_STT_DEFAULT_LANGUAGE", "en-US")
        language = default_language if isinstance(default_language, str) and default_language else "en-US"
        enable_partials = True

        if FEATURE_LEGACY_POLICY and isinstance(ctx.policy_snapshot, Mapping):
            policy_block = (
                ctx.policy_snapshot.get("policy")
                if isinstance(ctx.policy_snapshot, Mapping)
                else None
            )
        else:
            policy_block = ctx.policy if isinstance(ctx.policy, Mapping) else None

        if isinstance(policy_block, Mapping):
            nlu_block = policy_block.get("nlu")
            if isinstance(nlu_block, Mapping):
                lang_candidate = nlu_block.get("language")
                if isinstance(lang_candidate, str) and lang_candidate.strip():
                    language = lang_candidate.strip()

            asr_block = policy_block.get("asr")
            if isinstance(asr_block, Mapping):
                partials_flag = asr_block.get("enable_partials")
                if isinstance(partials_flag, bool):
                    enable_partials = partials_flag

        config_map = {"language": language, "enable_partials": enable_partials}
        ctx.active_asr_config = dict(config_map)
        return config_map

    def _resolve_asr_sample_rate(self, ctx: AdapterContext) -> int:
        profile = getattr(ctx.session, "audio_profile", None)
        if isinstance(profile, Mapping):
            candidate = profile.get("sample_rate")
            try:
                parsed = int(candidate)
            except (TypeError, ValueError):
                parsed = None
            if parsed == 16000:
                return parsed

        default_rate = getattr(config, "GCP_STT_DEFAULT_SAMPLE_RATE", _DEFAULT_GCP_SAMPLE_RATE_HZ)
        try:
            fallback_rate = int(default_rate)
        except (TypeError, ValueError):
            fallback_rate = _DEFAULT_GCP_SAMPLE_RATE_HZ
        if fallback_rate <= 0:
            fallback_rate = _DEFAULT_GCP_SAMPLE_RATE_HZ
        return fallback_rate

    def _create_asr_engine(self, ctx: AdapterContext) -> ASREngine:
        """Create the ASR engine for the session."""

        return GCPStreamingASREngine()

    async def _handle_asr_result(
        self, ctx: AdapterContext, transcript: str | None, is_final: bool
    ) -> None:
        # Safety: only process if the stream is currently open and ours.
        if ctx.session.asr_state != "open" or not ctx.asr_open or not ctx.asr_stream_id:
            _log.info(
                "evt=asr_result_ignored sid=%s reason=asr_not_open state=%s stream=%s",
                ctx.sid,
                ctx.session.asr_state,
                ctx.asr_stream_id,
            )
            return
        text = transcript or ""
        vendor = "gcp"

        if not isinstance(text, str):
            text = str(text)

        if not is_final:
            ctx.last_asr_partial = text
        else:
            ctx.last_asr_partial = None

        meta: Dict[str, Any] = {"vendor": vendor}
        # Correlators
        req_for_events = ctx.asr_stream_req_id or ctx.await_user_req_id
        if req_for_events:
            meta["req_id"] = req_for_events
        meta["stream_id"] = ctx.asr_stream_id
        event_payload: Dict[str, Any]

        if not is_final:
            ctx.partial_seq += 1
            meta["partial_seq"] = ctx.partial_seq
            event_payload = {
                "type": EVT_ASR_PARTIAL,
                "sid": ctx.sid,
                "text": text,
                "vendor": vendor,
                "meta": dict(meta),
                "req_id": req_for_events,
            }
        else:
            if not text.strip():
                meta["no_speech"] = True
            event_payload = {
                "type": EVT_ASR_FINAL,
                "sid": ctx.sid,
                "text": text,
                "vendor": vendor,
                "meta": dict(meta),
                "req_id": req_for_events,
            }
            ctx.asr_final_emitted = True

        bus.publish(event_payload)

        now_ms = self._now_ms()
        ctx.session.last_vendor_activity_ms = float(now_ms)

        if not is_final:
            req_id = ctx.await_user_req_id if isinstance(ctx.await_user_req_id, str) else ""
            await self._invoke_engine("on_asr_partial", ctx.sid, req_id, 1.0, text)
            return

        req_id_final = ctx.await_user_req_id if isinstance(ctx.await_user_req_id, str) else None
        await self._invoke_engine("on_asr_final", ctx.sid, text, req_id_final)
        await self._close_asr(ctx, reason="final_transcript")

    def _schedule_asr_open(self, ctx: AdapterContext) -> None:
        if ctx.ws_send is None:
            raise RuntimeError("websocket send unavailable for asr.open")
        if not can_open(ctx.session):
            return
        if ctx.session.tts_active and not self._allow_capture_during_tts(ctx):
            ctx.session.queued_arm = True
            return
        if ctx.asr_open_task and not ctx.asr_open_task.done():
            return

        mark(ctx.session, "opening")
        ctx.asr_bytes_sent = 0
        ctx.asr_opened_ms = None
        ctx.asr_close_reason = None
        ctx.asr_final_emitted = False
        ctx.asr_closed_ack_sent = False
        ctx.session.first_chunk_sent = False
        ctx.session.queued_arm = False
        ctx.session.closed_at_ms = None
        ctx.asr_first_packet_logged = False
        ctx.asr_silence_hold_logged = False
        ctx.asr_silence_eot_logged = False
        ctx.asr_vendor = "gcp"
        ctx.client_turn_closed = False
        ctx.last_asr_partial = None

        try:
            ctx.asr_open_task = asyncio.create_task(self._open_asr(ctx))
        except RuntimeError:
            loop = asyncio.get_running_loop()
            ctx.asr_open_task = loop.create_task(self._open_asr(ctx))

    async def _open_asr(self, ctx: AdapterContext) -> None:
        send = ctx.ws_send
        if send is None:
            mark(ctx.session, "closed")
            ctx.asr_open_task = None
            ctx.asr_open = False
            return

        engine = ctx.session.asr_engine
        if engine is not None and not getattr(engine, "_closed", True):
            _log.info("evt=asr_open_dedup sid=%s vendor=gcp", ctx.sid)
            await self._publish(
                ASR_OPEN_DEDUP,
                ctx.sid,
                {"state": ctx.session.asr_state, "vendor": "gcp"},
            )
            ctx.asr_open_task = None
            return

        engine = self._create_asr_engine(ctx)
        ctx.session.asr_engine = engine

        # New stream identity + req snapshot
        stream_id = uuid.uuid4().hex
        ctx.asr_stream_id = stream_id
        # Freeze the request id that this ASR stream belongs to (if any)
        ctx.asr_stream_req_id = ctx.await_user_req_id

        asr_config = self._resolve_asr_config(ctx)
        sample_rate = self._resolve_asr_sample_rate(ctx)
        language = asr_config.get("language") or getattr(
            config, "GCP_STT_DEFAULT_LANGUAGE", "en-US"
        )

        async def _on_result(transcript: str, is_final: bool, _sid=stream_id) -> None:
            # Drop late or alien results
            if _sid != ctx.asr_stream_id or ctx.session.asr_state != "open":
                _log.info(
                    "evt=asr_result_dropped sid=%s reason=stale stream=%s current=%s",
                    ctx.sid,
                    _sid,
                    ctx.asr_stream_id,
                )
                return
            await self._handle_asr_result(ctx, transcript, is_final)

        try:
            _log.info(
                "evt=asr_open_begin sid=%s vendor=gcp sample_rate=%s language=%s",
                ctx.sid,
                sample_rate,
                language,
            )
            await engine.open(
                sample_rate=sample_rate,
                language=language,
                sid=ctx.sid,
                on_result=_on_result,
            )
        except asyncio.CancelledError:
            mark(ctx.session, "closed")
            ctx.asr_open_task = None
            ctx.asr_open = False
            raise
        except Exception:
            ctx.session.asr_engine = None
            mark(ctx.session, "closed")
            ctx.asr_open_task = None
            ctx.asr_open = False
            _log.exception("evt=asr_open_failed sid=%s vendor=gcp", ctx.sid)
            try:
                await self._send_asr_error(send, ctx, "open_failed")
            except Exception:
                _log.exception("evt=asr_open_error_send sid=%s", ctx.sid)
            await self._publish(
                ASR_SINGLE_STREAM_INVARIANT,
                ctx.sid,
                {"state": "closed", "ok": False, "reason": "open_failed"},
            )
            return

        ctx.asr_open_task = None
        ctx.session.queued_arm = False
        mark(ctx.session, "open")
        ctx.asr_opened_ms = self._now_ms()
        ctx.asr_close_reason = None
        ctx.session.first_chunk_sent = False
        ctx.asr_open = True

        await self._publish(
            ASR_SINGLE_STREAM_INVARIANT,
            ctx.sid,
            {"state": "open", "ok": True, "vendor": "gcp"},
        )

        bus.publish(
            {
                "type": EVT_ASR_OPEN,
                "sid": ctx.sid,
                "who": "server",
                "source": "ws.adapter",
                "vendor": "gcp",
            }
        )
        bus.publish(
            {
                "type": EVT_ASR_READY,
                "sid": ctx.sid,
                "who": "server",
                "source": "ws.adapter",
                "vendor": "gcp",
            }
        )

    async def _close_asr(
        self,
        ctx: AdapterContext,
        *,
        reason: Optional[str] = None,
    ) -> None:
        task = ctx.asr_open_task
        ctx.asr_open_task = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        engine = ctx.session.asr_engine
        ctx.session.asr_engine = None
        ctx.asr_open = False

        if ctx.session.asr_state in {"opening", "open"}:
            mark(ctx.session, "closing")

        if engine is not None:
            try:
                await engine.close()
            except Exception:
                _log.warning("evt=asr_close_failed sid=%s vendor=gcp", ctx.sid, exc_info=True)

        mark(ctx.session, "closed")
        ctx.session.first_chunk_sent = False
        ctx.session.queued_arm = False
        ctx.asr_ready = False
        if reason:
            ctx.asr_close_reason = reason
        ctx.session.eot_armed = False
        ctx.session.server_vad_speech = False
        ctx.session.server_vad_since_ms = None
        ctx.last_asr_partial = None
        ctx.asr_stream_id = None
        ctx.asr_stream_req_id = None

        await self._publish(
            ASR_SINGLE_STREAM_INVARIANT,
            ctx.sid,
            {"state": "closed", "ok": True, "reason": reason, "vendor": "gcp"},
        )

        bus.publish(
            {
                "type": EVT_ASR_CLOSED,
                "sid": ctx.sid,
                "who": "server",
                "source": "ws.adapter",
                "vendor": "gcp",
            }
        )

    def _policy_keep_warm_ms(self, ctx: AdapterContext) -> int:
        if FEATURE_LEGACY_POLICY and isinstance(ctx.policy_snapshot, Mapping):
            policy_block = (
                ctx.policy_snapshot.get("policy")
                if isinstance(ctx.policy_snapshot, Mapping)
                else None
            )
        else:
            policy_block = ctx.policy if isinstance(ctx.policy, Mapping) else None
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

    def _prepare_asr_turn_begin(self, ctx: AdapterContext, reason: str) -> Optional[Dict[str, Any]]:
        if ctx.asr_turn_active:
            return None
        ctx.asr_turn_active = True
        ctx.asr_turn_begin_sent = True
        ctx.asr_turn_armed_sent = False
        ctx.asr_first_packet_logged = False
        ctx.asr_silence_hold_logged = False
        ctx.asr_silence_eot_logged = False
        ctx.asr_bytes_sent = 0
        ctx.asr_first_packet_monotonic = None
        self._bus("asr.turn.begin", {"sid": ctx.sid, "reason": reason})
        return {"type": "asr.turn", "state": "begin"}

    def _emit_asr_turn_armed(self, ctx: AdapterContext) -> None:
        if not ctx.asr_turn_active or ctx.asr_turn_armed_sent:
            return
        ctx.asr_turn_armed_sent = True
        self._bus("asr.turn.armed", {"sid": ctx.sid})

    def _prepare_asr_turn_end(self, ctx: AdapterContext, reason: str) -> Optional[Dict[str, Any]]:
        if not ctx.asr_turn_active:
            return None
        ctx.asr_turn_active = False
        ctx.asr_turn_begin_sent = False
        ctx.asr_turn_armed_sent = False
        self._bus("asr.turn.end", {"sid": ctx.sid, "reason": reason})
        return {"type": "asr.turn", "state": "end"}

    async def _send_asr_ready_bundle(
        self,
        send: Callable[[dict], Awaitable[None]],
        ctx: AdapterContext,
    ) -> None:
        if ctx.asr_ready_bundle_sent_ms is not None:
            return
        mode = ctx.audio_pipeline_mode or "pcm16"
        descriptor = dict(self._input_descriptor_for_mode(mode))
        timeslice_ms = self._resolve_capture_timeslice(ctx, mode)
        vendor = ctx.asr_vendor or "gcp"
        session_policy = ctx.session_capture_policy or self._session_capture_policy_for_mode(mode)
        now_ms = int(time.time() * 1000)
        input_payload = dict(descriptor)
        input_payload.setdefault("mode", mode)
        input_payload["mime"] = descriptor.get("mime", "")
        input_payload["timeslice_ms"] = timeslice_ms
        asr_ready_frame = {
            "type": "asr.ready",
            "ts_ms": now_ms,
            "vendor": vendor,
            "input": input_payload,
        }
        asr_ready_frame["sid"] = ctx.sid
        if session_policy:
            asr_ready_frame["policy"] = session_policy

        capture = dict(descriptor)
        capture["timeslice_ms"] = timeslice_ms
        capture["manual_gate"] = False
        input_start = {
            "type": "input.start",
            "capture": capture,
        }
        if session_policy:
            input_start["policy"] = session_policy

        start_payload: Dict[str, Any] = {"type": "start_listening"}
        if session_policy:
            start_payload["policy"] = session_policy

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

        try:
            await self._send_json(send, ctx.sid, asr_ready_frame)
            _log.info(
                "evt=asr_ready_bundle_sent sid=%s input.mode=%s input.mime=%s capture.timeslice_ms=%s vendor=%s",
                ctx.sid,
                mode,
                descriptor.get("mime", ""),
                timeslice_ms,
                vendor,
            )
        except Exception:
            _log.warning("evt=asr_ready_bundle_send_failed sid=%s", ctx.sid, exc_info=True)

        self._mark_input_start(ctx)
        await self._send_json(send, ctx.sid, input_start)
        ctx.client_mic_open = True  # allow first audio chunk through immediately
        ctx.pending_start_listening = dict(start_payload)
        ctx.pending_start_listening_sent = False
        if ctx.asr_ready:
            await self._send_json(send, ctx.sid, start_payload)
            ctx.pending_start_listening = None
            ctx.pending_start_listening_sent = False
        turn_begin_payload = self._prepare_asr_turn_begin(ctx, "ready_bundle")
        if turn_begin_payload is not None:
            try:
                await self._send_json(send, ctx.sid, turn_begin_payload)
            except Exception:  # pragma: no cover - defensive logging
                _log.warning("evt=asr_turn_begin_send_failed sid=%s", ctx.sid, exc_info=True)
            else:
                if not ctx.turn_active:
                    try:
                        await self._send_json(send, ctx.sid, {"type": "turn.begin"})
                    except Exception:  # pragma: no cover - defensive logging
                        _log.warning(
                            "evt=turn_begin_send_failed sid=%s reason=ready_bundle",
                            ctx.sid,
                            exc_info=True,
                        )
                    else:
                        ctx.turn_active = True

        self._schedule_no_audio_watchdog_rearm(ctx)
        

    @staticmethod
    def _decode_headers(headers: Iterable[tuple[bytes, bytes]]) -> Dict[str, str]:
        decoded: Dict[str, str] = {}
        for key, value in headers:
            decoded[key.decode("latin1").lower()] = value.decode("latin1")
        return decoded

    @staticmethod
    def _client_offers_permessage_deflate(headers: Mapping[str, str]) -> bool:
        value = headers.get("sec-websocket-extensions")
        if not isinstance(value, str):
            return False
        return "permessage-deflate" in value.lower()

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

    async def _maybe_emit_audio_throttle(
        self,
        send: Callable[[dict], Awaitable[None]] | None,
        ctx: AdapterContext,
        reason: str = "audio_backlog",
    ) -> None:
        if send is None:
            return
        now = int(time.time() * 1000)
        if getattr(ctx.session, "tts_active", False):
            return
        sp = self._server_policy(ctx)
        try:
            grace_ms = int(sp.get("throttle_grace_ms", self.THROTTLE_GRACE_AFTER_READY_MS))
        except Exception:
            grace_ms = self.THROTTLE_GRACE_AFTER_READY_MS
        ready_sent = ctx.asr_ready_bundle_sent_ms
        if ready_sent and (now - ready_sent) < grace_ms:
            return
        if (now - getattr(self, "_last_throttle_emit_ms", 0)) < self.THROTTLE_COOLDOWN_MS:
            return
        backlog_ok = False
        if ctx.ing_chunks >= self.THROTTLE_BACKLOG_FRAMES:
            backlog_ok = True
        elif ctx.ing_last_tick_t0_ms is not None:
            if (now - ctx.ing_last_tick_t0_ms) > self.THROTTLE_BACKLOG_MS:
                backlog_ok = True
        if not backlog_ok:
            return
        try:
            await self._send_json(send, ctx.sid, {"type": "audio.throttle", "ms": self.THROTTLE_BURST_MS})
            self._last_throttle_emit_ms = now
            _log.info(
                "evt=audio_throttle_emit sid=%s reason=%s ms=%s",
                ctx.sid,
                reason,
                self.THROTTLE_BURST_MS,
            )
        except Exception:
            _log.warning("evt=audio_throttle_emit_failed sid=%s", ctx.sid, exc_info=True)

    async def _update_backpressure(self, ctx: AdapterContext, queued: int) -> None:
        ctx.outbound_queue_depth = queued
        send = ctx.ws_send
        if ctx.backpressure_state == "off" and queued > QUEUE_ON_THRESHOLD:
            ctx.backpressure_state = "on"
            if send is not None:
                await self._maybe_emit_audio_throttle(send, ctx, reason="backpressure_on")
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
