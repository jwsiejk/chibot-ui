"""Vendor-agnostic ASR runtime for the voice v2 engine."""
from __future__ import annotations

import asyncio
import logging
import math
import os
import struct
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, Mapping, Optional

from app.config import (
    ASR_BACKPRESSURE_THRESHOLD_BYTES,
    ASR_IDLE_CLOSE_MS,
    ASR_TRACE,
)
from app.policy.model import Policy
from app.telemetry import bus
from app.voice_v2 import (
    EVT_ASR_FINAL,
    EVT_ASR_OPEN,
    EVT_ASR_PARTIAL,
    EVT_ASR_READY,
    EVT_WS_JSON_RECV,
    EVT_WS_JSON_SEND,
)
from app.voice_v2.engine import EngineV2

_log = logging.getLogger(__name__)

_DG_LISTEN_URL = "wss://api.deepgram.com/v1/listen"
_PARTIAL_CONFIDENCE = 0.55
_FINAL_CONFIDENCE = 0.9
_DEFAULT_IDLE_CLOSE_MS = 4000
_BACKPRESSURE_THRESHOLD = max(0, ASR_BACKPRESSURE_THRESHOLD_BYTES)
_TRACE_ENABLED = bool(ASR_TRACE)
_EBML_MAGIC = b"\x1a\x45\xdf\xa3"
_RMS_PROBE_WINDOW_MS = 2000
def _resolve_stream_open_timeout(default: float = 10.0) -> float:
    raw = os.getenv("DG_STREAM_OPEN_TIMEOUT_S")
    if raw is None:
        return default
    try:
        timeout = float(raw)
    except (TypeError, ValueError):
        return default
    if timeout <= 0:
        return default
    return timeout


_STREAM_OPEN_TIMEOUT_S = _resolve_stream_open_timeout()
_NO_AUDIO_TIMEOUT_S = 9.0
_LISTENING_STATE = "Listening"
_READY_STATES = {"Ready", "Idle"}


def _safe_url(url: str | None) -> str:
    if not url:
        return ""
    try:
        from urllib.parse import urlparse, urlunparse

        u = urlparse(url)
        # strip query to avoid leaking keys; keep scheme/host/path
        return urlunparse((u.scheme, u.netloc, u.path, "", "", ""))
    except Exception:
        return ""


def _default_input_descriptor() -> Dict[str, Any]:
    return {
        "container": "webm",
        "codec": "opus",
        "rate_hz": 48000,
        "channels": 1,
    }


def _pcm_input_descriptor() -> Dict[str, Any]:
    return {
        "container": "raw",
        "codec": "pcm_s16le",
        "rate_hz": 16000,
        "channels": 1,
    }


@dataclass
class _SessionState:
    """Track per-session streaming state."""

    sid: str
    pending: Deque[bytes] = field(default_factory=deque)
    buffered_bytes: int = 0
    req_id: Optional[str] = None
    stream_open_task: Optional[asyncio.Task[None]] = None
    stream_open: bool = False
    last_audio_ts: float = 0.0
    idle_handle: asyncio.TimerHandle | None = None
    stream_id: Optional[str] = None
    close_reason: Optional[str] = None
    opened_at_ms: int = 0
    opened_at_monotonic: float = 0.0
    last_audio_ts_ms: int = 0
    chunks_sent: int = 0
    bytes_sent: int = 0
    dg_msgs_partial: int = 0
    dg_msgs_final: int = 0
    finals_delivered: int = 0
    dropped_chunks: int = 0
    last_stream_id: Optional[str] = None
    ready_watchdog: asyncio.TimerHandle | None = None
    ready_armed_at: float = 0.0
    prearm_requested: bool = False
    input_desc: Dict[str, Any] = field(default_factory=_default_input_descriptor)
    unavailable_emitted: bool = False
    first_chunk_seen: bool = False
    rollup_window_start: float = 0.0
    rollup_chunks: int = 0
    rollup_bytes: int = 0
    bytes_received: int = 0
    listening: bool = False
    ingress_packets: int = 0
    ingress_bytes: int = 0
    first_ingress_ms: int = 0
    first_partial_logged: bool = False
    last_partial_log: float = 0.0
    model: Optional[str] = None
    keep_warm_until: float = 0.0
    probe_active: bool = False
    probe_target_samples: int = 0
    probe_samples_collected: int = 0
    probe_sum_squares: float = 0.0
    probe_peak: float = 0.0
    probe_window_ms: int = _RMS_PROBE_WINDOW_MS
    commit_on_vad_silence: bool = True
    commit_silence_ms: int = 900
    max_utterance_ms: int = 8000
    commit_timer: asyncio.TimerHandle | None = None
    utterance_active: bool = False
    utterance_started_at: float = 0.0

    def __post_init__(self) -> None:
        now_monotonic = time.monotonic()
        now_wall = time.time()
        self.opened_at_monotonic = now_monotonic
        self.opened_at_ms = int(now_wall * 1000)
        self.last_audio_ts = now_monotonic
        self.last_audio_ts_ms = int(now_wall * 1000)


class ASRRuntime:
    """Bridge websocket audio to realtime transcription providers."""

    def __init__(
        self,
        engine: EngineV2,
        client: Any,
        *,
        telemetry_bus: Any | None = None,
    ) -> None:
        if engine is None:
            raise ValueError("engine must be provided")
        if client is None:
            raise ValueError("client must be provided")

        self._engine = engine
        self._client = client
        self._vendor = self._detect_vendor(client)
        self._bus = telemetry_bus or bus
        if self._vendor == "deepgram":
            self._configure_client()
        self._sessions: Dict[str, _SessionState] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self.policy = Policy()
        configured_idle = ASR_IDLE_CLOSE_MS
        if not isinstance(configured_idle, (int, float)):
            configured_idle = _DEFAULT_IDLE_CLOSE_MS
        client_idle = getattr(client, "idle_close_ms", None)
        if isinstance(client_idle, (int, float)) and client_idle >= 0:
            self._idle_close_ms = int(client_idle)
        else:
            self._idle_close_ms = int(configured_idle)
        if self._idle_close_ms < 0:
            self._idle_close_ms = _DEFAULT_IDLE_CLOSE_MS
        self._ws_json_subscription = None
        self._ws_send_subscription = None
        self._client_telemetry_subscription = None
        subscribe = getattr(self._bus, "subscribe", None)
        if callable(subscribe):
            try:
                self._ws_json_subscription = subscribe(
                    EVT_WS_JSON_RECV, self._handle_ws_json_event
                )
            except Exception:  # pragma: no cover - defensive logging
                _log.exception("evt=asr_runtime_subscribe_failed")
            try:
                self._ws_send_subscription = subscribe(
                    EVT_WS_JSON_SEND, self._handle_ws_send_event
                )
            except Exception:  # pragma: no cover - defensive logging
                _log.exception("evt=asr_runtime_send_subscribe_failed")
            try:
                self._client_telemetry_subscription = subscribe(
                    "EVT_AUDIO_CHUNK_SENT_CLIENT",
                    self._handle_client_audio_event,
                )
            except Exception:  # pragma: no cover - defensive logging
                _log.exception("evt=asr_runtime_client_telemetry_subscribe_failed")

    def set_bus(self, telemetry_bus: Any | None) -> None:
        if telemetry_bus is None:
            return
        self._bus = telemetry_bus

    @staticmethod
    def _detect_vendor(client: Any) -> str:
        vendor = getattr(client, "vendor", None)
        if isinstance(vendor, str) and vendor.strip():
            return vendor.strip().lower()
        class_name = type(client).__name__.lower()
        module_name = getattr(type(client), "__module__", "").lower()
        if "speechmatics" in class_name or "speechmatics" in module_name:
            return "speechmatics"
        return "deepgram"

    def _configure_client(self) -> None:
        """Force the Deepgram client to use the containerized listen URL."""

        target_url = _DG_LISTEN_URL
        current_url = getattr(self._client, "_url", None)
        if current_url == target_url:
            return
        try:
            setattr(self._client, "_url", target_url)
        except Exception:  # pragma: no cover - defensive
            _log.debug(
                "evt=asr_runtime_set_listen_url_failed target_url=%s", target_url,
                exc_info=True,
            )
        else:
            _log.debug(
                "evt=asr_runtime_listen_url_set url=%s", target_url
            )

    def _input_descriptor_from_policy(
        self, policy_snapshot: Mapping[str, Any] | Any | None
    ) -> Dict[str, Any]:
        if policy_snapshot is None:
            return _default_input_descriptor()

        media: Mapping[str, Any] | Any | None
        if isinstance(policy_snapshot, Mapping):
            media = policy_snapshot.get("media")
        else:
            media = getattr(policy_snapshot, "media", None)

        if media is None:
            return _default_input_descriptor()

        if isinstance(media, Mapping):
            get_value = media.get
        else:
            get_value = lambda key, default=None: getattr(media, key, default)

        asr_input = get_value("asr_input")
        if asr_input == "webm_opus":
            rate = get_value("asr_rate_hz", 48000)
            channels = get_value("asr_channels", 1)
            rate_value = rate if isinstance(rate, int) and rate > 0 else 48000
            channels_value = (
                channels if isinstance(channels, int) and channels > 0 else 1
            )
            return {
                "container": "webm",
                "codec": "opus",
                "rate_hz": rate_value,
                "channels": channels_value,
            }

        if asr_input == "pcm_16k":
            return _pcm_input_descriptor()

        if asr_input:
            self._emit_log(
                {
                    "type": "EVT_LOG",
                    "level": "ERROR",
                    "msg": f"evt=asr_policy_invalid input={asr_input}",
                }
            )

        return _default_input_descriptor()

    # ------------------------------------------------------------------
    # Websocket hooks
    # ------------------------------------------------------------------
    def on_ws_open(self, sid: str) -> None:
        self._ensure_loop()
        state = self._sessions.get(sid)
        if state is None:
            state = _SessionState(sid=sid)
            self._sessions[sid] = state
        _log.info("evt=asr_on_ws_open sid=%s", sid)
        state.listening = False
        state.prearm_requested = False
        state.bytes_received = 0
        state.ingress_packets = 0
        state.ingress_bytes = 0
        state.first_ingress_ms = 0
        state.first_partial_logged = False
        state.last_partial_log = 0.0
        state.probe_active = False
        state.probe_target_samples = 0
        state.probe_samples_collected = 0
        state.probe_sum_squares = 0.0
        state.probe_peak = 0.0
        state.utterance_active = False
        self._cancel_commit_timer(state)
        self._apply_commit_policy(state)

    def on_ws_audio(self, sid: str, chunk: bytes) -> None:
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise TypeError("chunk must be bytes-like")
        data = bytes(chunk)
        if not data:
            return

        state = self._sessions.get(sid)
        if state is None:
            state = _SessionState(sid=sid)
            self._sessions[sid] = state

        if not state.listening:
            return

        if not state.utterance_active:
            state.utterance_active = True
            self._apply_commit_policy(state)
            self._start_commit_timer(sid, state)

        state.unavailable_emitted = False

        now_monotonic = time.monotonic()
        state.last_audio_ts = now_monotonic
        now_ms = int(time.time() * 1000)
        state.last_audio_ts_ms = now_ms
        if state.ready_watchdog is not None and state.ready_armed_at:
            self._cancel_ready_watchdog(state)
        chunk_len = len(data)

        if state.ingress_packets == 0:
            state.first_ingress_ms = now_ms
        state.ingress_packets += 1
        state.ingress_bytes += chunk_len

        if (
            state.stream_open
            and state.bytes_received > 0
            and data.startswith(_EBML_MAGIC)
        ):
            stream_id = state.stream_id or state.last_stream_id or ""
            _log.warning(
                "evt=asr_midstream_header sid=%s stream_id=%s action=reopen_vendor",
                sid,
                stream_id,
            )
            state.close_reason = "midstream_header"
            try:
                self._client.close_stream(sid)
            except Exception:  # pragma: no cover - defensive
                _log.exception("evt=asr_midstream_close_failed sid=%s", sid)
            finally:
                self._finalize_rms_probe(sid, state)
            state.stream_open = False
            state.stream_id = None
            state.bytes_received = 0
            state.chunks_sent = 0
            state.pending.clear()
            state.buffered_bytes = 0
            state.first_chunk_seen = False

        if not state.first_chunk_seen:
            state.first_chunk_seen = True
            state.rollup_window_start = now_monotonic
            state.rollup_chunks = 0
            state.rollup_bytes = 0
            _log.info(
                "evt=audio_first_chunk sid=%s bytes=%d codec=webm_opus",
                sid,
                chunk_len,
            )
        if not state.rollup_window_start:
            state.rollup_window_start = now_monotonic
        state.rollup_chunks += 1
        state.rollup_bytes += chunk_len
        if (
            state.rollup_window_start
            and now_monotonic - state.rollup_window_start >= 1.0
        ):
            _log.debug(
                "evt=audio_rollup sid=%s chunks=%d bytes=%d window_ms=1000",
                sid,
                state.rollup_chunks,
                state.rollup_bytes,
            )
            state.rollup_window_start = now_monotonic
            state.rollup_chunks = 0
            state.rollup_bytes = 0

        if state.stream_id is None:
            state.stream_id = f"dg-stream-{uuid.uuid4().hex}"
            state.last_stream_id = state.stream_id

        if not state.stream_open:
            state.pending.append(data)
            state.buffered_bytes += chunk_len
            self._apply_backpressure(sid, state)
            if state.pending:
                self._ensure_stream(sid, state)
        else:
            self._forward_chunk(sid, state, data)

        if _TRACE_ENABLED:
            _log.debug(
                "evt=asr_audio_enqueued sid=%s stream_id=%s bytes=%d buffered_bytes=%d pending_chunks=%d",
                sid,
                state.stream_id or state.last_stream_id or "",
                chunk_len,
                state.buffered_bytes,
                len(state.pending),
            )

        self._reset_idle_timer(sid, state)

    def on_ws_close(self, sid: str) -> None:
        state = self._sessions.pop(sid, None)
        if state is None:
            return
        state.close_reason = "client_ws_closed"
        self._cancel_idle_timer(state)
        self._cancel_ready_watchdog(state)
        self._cancel_commit_timer(state)
        task = state.stream_open_task
        if task is not None and not task.done():
            task.cancel()
        state.stream_open = False
        try:
            self._client.close_stream(sid)
        except Exception:  # pragma: no cover - defensive
            _log.exception("evt=asr_stream_close_failed sid=%s", sid)
        finally:
            self._finalize_rms_probe(sid, state)
            self._log_session_rollup(state)
        state.req_id = None
        state.bytes_received = 0
        state.listening = False
        state.utterance_active = False

    def prearm(self, sid: str, *, keep_warm_ms: int | None = None) -> None:
        """Request that the ASR stream open proactively for the session."""

        if not isinstance(sid, str) or not sid:
            return

        state = self._sessions.get(sid)
        if state is None:
            state = _SessionState(sid=sid)
            self._sessions[sid] = state
        if state.stream_open:
            if keep_warm_ms is not None and keep_warm_ms > 0:
                state.keep_warm_until = max(
                    state.keep_warm_until,
                    time.monotonic() + (keep_warm_ms / 1000.0),
                )
            return

        state.prearm_requested = True
        if keep_warm_ms is not None and keep_warm_ms > 0:
            state.keep_warm_until = max(
                state.keep_warm_until,
                time.monotonic() + (keep_warm_ms / 1000.0),
            )
        self._ensure_stream(sid, state)

    async def open_if_needed(self, sid: str, *, req_id: str | None = None) -> None:
        """Ensure the streaming session is open before capturing microphone audio."""

        if not isinstance(sid, str) or not sid:
            return

        loop = self._ensure_loop()
        if loop is None:
            return

        state = self._sessions.get(sid)
        if state is None:
            state = _SessionState(sid=sid)
            self._sessions[sid] = state

        if isinstance(req_id, str) and req_id:
            state.req_id = req_id

        if state.stream_open:
            return

        state.prearm_requested = True
        self._ensure_stream(sid, state)

        deadline = time.monotonic() + max(0.0, _STREAM_OPEN_TIMEOUT_S)
        while not state.stream_open:
            task = state.stream_open_task
            now = time.monotonic()
            if task is None or task.done():
                if now >= deadline:
                    break
                self._ensure_stream(sid, state)
                await asyncio.sleep(0.01)
                continue
            remaining = deadline - now
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(task, timeout=remaining)
            except asyncio.TimeoutError:
                break
            except asyncio.CancelledError:
                raise

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _handle_ws_json_event(self, event: Mapping[str, Any]) -> None:
        if not isinstance(event, Mapping):
            return
        if event.get("type") != EVT_WS_JSON_RECV:
            return
        sid = event.get("sid")
        if not isinstance(sid, str) or not sid:
            return
        state = self._sessions.get(sid)
        if state is None:
            return
        payload = event.get("payload")
        meta: Mapping[str, Any] | None = None
        if isinstance(payload, Mapping):
            candidate = payload.get("meta")
            if isinstance(candidate, Mapping):
                meta = candidate
        frame_type = meta.get("frame_type") if meta is not None else None
        if frame_type != "asr.rearm.request":
            return
        if state.stream_open:
            return
        try:
            self.prearm(sid)
        except Exception:  # pragma: no cover - defensive logging
            _log.exception("evt=asr_runtime_rearm_failed sid=%s", sid)

    def _handle_ws_send_event(self, event: Mapping[str, Any]) -> None:
        if not isinstance(event, Mapping):
            return
        if event.get("type") != EVT_WS_JSON_SEND:
            return
        sid = event.get("sid")
        if not isinstance(sid, str) or not sid:
            return
        payload = event.get("payload") or event.get("frame")
        if not isinstance(payload, Mapping):
            return
        frame_type = payload.get("type")
        if frame_type == "start_listening":
            self._on_start_listening_frame(sid)
        elif frame_type in {"stop_listening", "input.stop"}:
            reason_value = payload.get("reason")
            reason = reason_value if isinstance(reason_value, str) else None
            self._on_stop_listening_frame(sid, reason)

    def _handle_client_audio_event(self, event: Mapping[str, Any]) -> None:
        if not isinstance(event, Mapping):
            return
        if event.get("type") != "EVT_AUDIO_CHUNK_SENT_CLIENT":
            return
        sid = event.get("sid")
        if not isinstance(sid, str) or not sid:
            return
        state = self._sessions.get(sid)
        if state is None:
            return
        meta = event.get("meta")
        if not isinstance(meta, Mapping):
            return
        if not meta.get("commit"):
            return
        self._apply_commit_policy(state)
        if not state.commit_on_vad_silence:
            return
        reason_val = meta.get("reason")
        reason = reason_val if isinstance(reason_val, str) and reason_val else "vad_silence"
        _log.info(
            "evt=asr_commit_trigger sid=%s vendor=%s source=client reason=%s",
            sid,
            self._vendor,
            reason,
        )
        self.commit(sid, reason=reason)

    def _on_start_listening_frame(self, sid: str) -> None:
        if not isinstance(sid, str) or not sid:
            return

        state = self._sessions.get(sid)
        if state is None:
            state = _SessionState(sid=sid)
            self._sessions[sid] = state

        state.listening = True
        state.bytes_received = 0
        state.first_chunk_seen = False
        state.rollup_window_start = 0.0
        state.rollup_chunks = 0
        state.rollup_bytes = 0
        state.ingress_packets = 0
        state.ingress_bytes = 0
        state.first_ingress_ms = 0
        state.first_partial_logged = False
        state.last_partial_log = 0.0
        state.pending.clear()
        state.buffered_bytes = 0
        state.prearm_requested = True
        state.utterance_active = False
        self._cancel_commit_timer(state)
        self._apply_commit_policy(state)
        self._ensure_stream(sid, state)

    def _on_stop_listening_frame(self, sid: str, reason: str | None) -> None:
        if not isinstance(sid, str) or not sid:
            return

        state = self._sessions.get(sid)
        if state is None:
            return

        state.listening = False
        state.prearm_requested = False
        state.pending.clear()
        state.buffered_bytes = 0
        state.bytes_received = 0
        state.first_chunk_seen = False
        state.rollup_window_start = 0.0
        state.rollup_chunks = 0
        state.rollup_bytes = 0
        state.ingress_packets = 0
        state.ingress_bytes = 0
        state.first_ingress_ms = 0
        state.first_partial_logged = False
        state.last_partial_log = 0.0
        state.utterance_active = False
        self._cancel_ready_watchdog(state)
        self._cancel_idle_timer(state)
        self._cancel_commit_timer(state)

        if state.stream_open:
            stream_id = state.stream_id or state.last_stream_id or ""
            state.close_reason = reason or "stop_listening"
            try:
                self._client.close_stream(sid)
            except Exception:  # pragma: no cover - defensive
                _log.exception("evt=asr_stop_listening_close_failed sid=%s", sid)
            finally:
                self._finalize_rms_probe(sid, state)
            state.stream_open = False
            state.stream_id = None
            state.req_id = None
            _log.info("evt=asr_stop_listening sid=%s stream_id=%s", sid, stream_id)
    def _emit_log(self, payload: Dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        event = dict(payload)
        event.setdefault("type", "EVT_LOG")
        try:
            self._bus.publish(event)
        except Exception:  # pragma: no cover - defensive
            _log.exception("evt=asr_emit_log_failed")

    def _ensure_loop(self) -> asyncio.AbstractEventLoop | None:
        loop = self._loop
        if loop is not None:
            return loop
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        self._loop = loop
        return loop

    def _apply_backpressure(self, sid: str, state: _SessionState) -> None:
        if _BACKPRESSURE_THRESHOLD <= 0:
            return
        if state.buffered_bytes <= _BACKPRESSURE_THRESHOLD:
            return

        dropped_chunks = 0
        while state.pending and state.buffered_bytes > _BACKPRESSURE_THRESHOLD:
            removed = state.pending.popleft()
            state.buffered_bytes = max(0, state.buffered_bytes - len(removed))
            dropped_chunks += 1

        if dropped_chunks:
            state.dropped_chunks += dropped_chunks
            stream_id = state.stream_id or state.last_stream_id or ""
            _log.info(
                "evt=asr_backpressure sid=%s stream_id=%s buffered_bytes=%d threshold=%d action=drop_oldest dropped_chunks=%d",
                sid,
                stream_id,
                state.buffered_bytes,
                _BACKPRESSURE_THRESHOLD,
                dropped_chunks,
            )

    def _log_session_rollup(self, state: _SessionState) -> None:
        stream_id = state.last_stream_id or state.stream_id or ""
        duration_ms = int(
            max(0.0, (time.monotonic() - state.opened_at_monotonic) * 1000)
        )
        msg = (
            "asr_rollup vendor=%s partials=%d finals=%d bytes=%d duration_ms=%d"
            % (
                self._vendor,
                state.dg_msgs_partial,
                state.dg_msgs_final,
                state.bytes_sent,
                duration_ms,
            )
        )
        _log.info(msg)
        if getattr(state, "ingress_packets", 0) >= 0:
            _log.info(
                "evt=audio_wire_rollup sid=%s ingress_packets=%s ingress_bytes=%s",
                state.sid,
                state.ingress_packets,
                state.ingress_bytes,
            )
        self._emit_log(
            {
                "type": "EVT_LOG",
                "logger": "app.voice_v2.asr_runtime",
                "level": "INFO",
                "sid": state.sid,
                "stream_id": stream_id,
                "msg": msg,
            }
        )

    def _log_utterance(
        self,
        state: _SessionState,
        stream_id: str,
        req_id: str,
        metadata: Dict[str, object],
        text: str,
    ) -> None:
        chars = int(metadata.get("len_chars") or len(text))
        words_meta = metadata.get("words")
        if isinstance(words_meta, (int, float)):
            words = max(0, int(words_meta))
        else:
            words = len([w for w in text.strip().split() if w])
        asr_latency_ms = int(metadata.get("latency_ms") or 0)
        log_template = (
            "evt=asr_utterance sid=%s stream_id=%s req_id=%s chars=%d words=%d "
            "asr_latency_ms=%d"
        )
        log_args = [
            state.sid,
            stream_id,
            req_id,
            chars,
            words,
            asr_latency_ms,
        ]
        e2e_latency = metadata.get("e2e_latency_ms")
        if isinstance(e2e_latency, (int, float)):
            log_template += " e2e_latency_ms=%d"
            log_args.append(int(e2e_latency))
        msg = log_template % tuple(log_args)
        _log.info(msg)
        self._emit_log(
            {
                "type": "EVT_LOG",
                "logger": "app.voice_v2.asr_runtime",
                "level": "INFO",
                "sid": state.sid,
                "stream_id": stream_id,
                "req_id": req_id,
                "msg": msg,
            }
        )

    def _ensure_stream(self, sid: str, state: _SessionState) -> None:
        loop = self._ensure_loop()
        if loop is None:
            _log.warning("evt=asr_no_loop sid=%s", sid)
            return
        if state.stream_open:
            return
        if not state.pending and not state.prearm_requested:
            return
        task = state.stream_open_task
        if task is not None and not task.done():
            return

        async def _open() -> None:
            if state.stream_id is None:
                state.stream_id = f"dg-stream-{uuid.uuid4().hex}"
            stream_id = state.stream_id
            state.last_stream_id = stream_id
            state.close_reason = None
            state.prearm_requested = False
            try:
                policy_snapshot = None
                try:
                    policy_snapshot = getattr(self._engine, "policy_snapshot", None)
                except Exception:  # pragma: no cover - defensive
                    policy_snapshot = None
                input_desc = self._input_descriptor_from_policy(policy_snapshot)
                state.input_desc = input_desc
                model_name = None
                if isinstance(policy_snapshot, Mapping):
                    candidate = policy_snapshot.get("model")
                    if isinstance(candidate, str) and candidate:
                        model_name = candidate
                    else:
                        interaction = policy_snapshot.get("interaction")
                        if isinstance(interaction, Mapping):
                            maybe_model = interaction.get("model")
                            if isinstance(maybe_model, str) and maybe_model:
                                model_name = maybe_model
                else:
                    candidate_model = getattr(policy_snapshot, "model", None)
                    if isinstance(candidate_model, str) and candidate_model:
                        model_name = candidate_model
                state.model = model_name if isinstance(model_name, str) and model_name else None
                _log.info(
                    "evt=asr_open_attempt sid=%s vendor=%s trigger=first_ingress_packet",
                    sid,
                    self._vendor,
                )
                open_timeout = max(5.0, float(_STREAM_OPEN_TIMEOUT_S))
                qs = await asyncio.wait_for(
                    self._client.open_stream(
                        sid,
                        on_partial=self._make_partial_cb(sid),
                        on_final=self._make_final_cb(sid),
                        on_error=self._make_error_cb(sid),
                        stream_id=stream_id,
                        on_close=self._make_close_cb(sid, state),
                        policy=policy_snapshot,
                    ),
                    timeout=open_timeout,
                )
                _log.info(
                    "evt=asr_opened sid=%s vendor=%s stream_id=%s",
                    sid,
                    self._vendor,
                    stream_id,
                )
                _log.info(
                    "evt=asr_vendor_info sid=%s vendor=%s transport=ws container=webm_opus model=%s",
                    sid,
                    self._vendor,
                    state.model or "auto",
                )
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError as err:
                last_url = getattr(self._client, "debug_last_url", None)
                self._emit_log(
                    {
                        "type": "EVT_LOG",
                        "logger": "app.voice_v2.asr_runtime",
                        "level": "ERROR",
                        "sid": sid,
                        "msg": (
                            "evt=asr_open_failed_timeout sid=%s vendor=%s url=%s timeout_s=%s"
                        )
                        % (sid, self._vendor, _safe_url(last_url), open_timeout),
                    }
                )
                _log.error(
                    "evt=asr_open_failed sid=%s vendor=%s err=%s",
                    sid,
                    self._vendor,
                    str(err) or "timeout",
                )
                _log.error(
                    "evt=asr_open_failed_timeout sid=%s vendor=%s url=%s timeout_s=%s",
                    sid,
                    self._vendor,
                    _safe_url(last_url),
                    open_timeout,
                )
                state.stream_id = None
                state.stream_open = False
                state.stream_open_task = None
                self._publish_asr_unavailable(
                    sid,
                    "open_failed",
                    f"stream open timed out after {open_timeout:.1f}s",
                    state=state,
                )
                loop = self._ensure_loop()
                if loop is not None:
                    loop.call_later(0.1, self._ensure_stream, sid, state)
                return
            except Exception as e:  # pragma: no cover - defensive
                # Surface DG status/close code & reason if the client exposes them
                code = getattr(e, "status", None) or getattr(e, "code", None)
                reason = (
                    getattr(e, "reason", None)
                    or getattr(e, "message", None)
                    or type(e).__name__
                )
                last_url = getattr(self._client, "debug_last_url", None)
                last_error = getattr(self._client, "last_error", None)
                _log.error(
                    "evt=asr_open_failed sid=%s vendor=%s err=%s",
                    sid,
                    self._vendor,
                    str(e),
                )
                self._emit_log(
                    {
                        "type": "EVT_LOG",
                        "logger": "app.voice_v2.asr_runtime",
                        "level": "ERROR",
                        "sid": sid,
                        "msg": (
                            "evt=asr_stream_open_failed sid=%s vendor=%s code=%s reason=%s url=%s last_error=%s"
                        )
                        % (
                            sid,
                            self._vendor,
                            code if code is not None else "",
                            reason or "",
                            _safe_url(last_url),
                            last_error if last_error is not None else "",
                        ),
                    }
                )
                _log.exception(
                    "evt=asr_stream_open_failed sid=%s code=%s reason=%s url=%s last_error=%s",
                    sid,
                    code,
                    reason,
                    _safe_url(last_url),
                    last_error,
                )
                state.stream_open = False
                state.stream_id = None
                state.stream_open_task = None
                detail_reason = reason or type(e).__name__
                if code is not None:
                    detail_reason = f"{detail_reason} (code={code})"
                self._publish_asr_unavailable(
                    sid,
                    "open_failed",
                    f"stream open failed: {detail_reason}",
                    state=state,
                )
                loop = self._ensure_loop()
                if loop is not None:
                    loop.call_later(0.5, self._ensure_stream, sid, state)
                return

            state.stream_open = True
            self._configure_rms_probe(state)
            state.first_chunk_seen = False
            state.rollup_window_start = 0.0
            state.rollup_chunks = 0
            state.rollup_bytes = 0
            state.unavailable_emitted = False
            state.bytes_received = 0
            mp = getattr(self.policy, "media", None)
            cp = getattr(self.policy, "capture", None)
            if mp is not None and cp is not None:
                self._emit_log(
                    {
                        "type": "EVT_LOG",
                        "logger": "app.voice_v2.asr_runtime",
                        "level": "INFO",
                        "sid": sid,
                        "msg": (
                            "evt=asr_policy input=%s rate_hz=%s ch=%s fallbacks=%s "
                            "start_on_asr_ready=%s start_on_turn_ready=%s timeslice_ms=%s "
                            "mask_during_tts=%s mask_keepalive_enable=%s mask_keepalive_ms=%s"
                        )
                        % (
                            getattr(mp, "asr_input", ""),
                            getattr(mp, "asr_rate_hz", ""),
                            getattr(mp, "asr_channels", ""),
                            getattr(mp, "fallbacks_allowed", ""),
                            getattr(cp, "start_on_asr_ready", ""),
                            getattr(cp, "start_on_turn_ready", ""),
                            getattr(cp, "timeslice_ms", ""),
                            getattr(cp, "mask_during_tts", ""),
                            getattr(cp, "mask_keepalive_enable", ""),
                            getattr(cp, "mask_keepalive_ms", ""),
                        ),
                    }
                )
            open_event = {"type": EVT_ASR_OPEN, "sid": sid, "vendor": self._vendor}
            if stream_id:
                open_event["stream_id"] = stream_id
            self._bus.publish(open_event)
            stream_id_value = stream_id if isinstance(stream_id, str) and stream_id else ""
            if not stream_id_value and isinstance(state.stream_id, str) and state.stream_id:
                stream_id_value = state.stream_id
            _log.info(
                "evt=asr_open sid=%s stream_id=%s vendor=%s qs=%s",
                sid,
                stream_id_value,
                self._vendor,
                qs or "",
            )
            ready_kwargs = {
                "sid": sid,
                "vendor": self._vendor,
                "stream_id": stream_id_value,
            }
            emit = getattr(self._bus, "emit", None)
            if callable(emit):
                try:
                    emit(EVT_ASR_READY, **ready_kwargs)
                except TypeError:
                    emit(EVT_ASR_READY, ready_kwargs)
            ready_event = {"type": EVT_ASR_READY, **ready_kwargs}
            self._bus.publish(ready_event)
            _log.info(
                "evt=asr_ready sid=%s vendor=%s stream_id=%s",
                sid,
                self._vendor,
                stream_id_value,
            )
            self._log_asr_rearmed(sid, state)

            asr_ready_frame = {
                "type": "asr.ready",
                "vendor": self._vendor,
                "input": dict(state.input_desc),
            }
            self._bus.publish(
                {
                    "type": EVT_WS_JSON_SEND,
                    "sid": sid,
                    "frame": asr_ready_frame,
                    "payload": asr_ready_frame,
                }
            )
            if stream_id:
                _log.info(
                    'evt=asr_session_open sid=%s stream_id=%s content_type="%s" idle_close_ms=%d',
                    sid,
                    stream_id,
                    "(not set)",
                    int(self._idle_close_ms),
                )
            pre_chunks_sent = state.chunks_sent
            while state.pending:
                chunk = state.pending.popleft()
                chunk_len = len(chunk)
                state.buffered_bytes = max(0, state.buffered_bytes - chunk_len)
                self._forward_chunk(sid, state, chunk)
            if state.chunks_sent == pre_chunks_sent:
                self._start_ready_watchdog(sid, state)
            else:
                self._cancel_ready_watchdog(state)
            state.stream_open_task = None
            self._reset_idle_timer(sid, state)

        def _on_done(_task: asyncio.Task[None]) -> None:
            if state.stream_open_task is _task:
                state.stream_open_task = None

        state.stream_open_task = loop.create_task(_open(), name=f"asr-stream-open-{sid}")
        state.stream_open_task.add_done_callback(_on_done)

    def _configure_rms_probe(self, state: _SessionState) -> None:
        desc: Mapping[str, Any] | None
        if isinstance(state.input_desc, Mapping):
            desc = state.input_desc
        else:
            try:
                desc = dict(state.input_desc)  # type: ignore[arg-type]
            except Exception:
                desc = None
        codec = str(desc.get("codec") or "") if isinstance(desc, Mapping) else ""
        if codec != "pcm_s16le":
            state.probe_active = False
            state.probe_target_samples = 0
            state.probe_samples_collected = 0
            state.probe_sum_squares = 0.0
            state.probe_peak = 0.0
            return

        rate_value = desc.get("rate_hz") if isinstance(desc, Mapping) else None
        try:
            rate_hz = int(rate_value) if rate_value is not None else 16000
        except (TypeError, ValueError):
            rate_hz = 16000
        if rate_hz <= 0:
            rate_hz = 16000

        target_samples = int(rate_hz * state.probe_window_ms / 1000)
        if target_samples <= 0:
            target_samples = int(16000 * state.probe_window_ms / 1000)
        if target_samples <= 0:
            target_samples = 1

        state.probe_active = True
        state.probe_target_samples = target_samples
        state.probe_samples_collected = 0
        state.probe_sum_squares = 0.0
        state.probe_peak = 0.0

    def _ingest_rms_probe(self, sid: str, state: _SessionState, chunk: bytes) -> None:
        if not state.probe_active:
            return

        remaining_samples = state.probe_target_samples - state.probe_samples_collected
        if remaining_samples <= 0:
            self._finalize_rms_probe(sid, state)
            return

        mv = memoryview(chunk)
        usable_bytes = min(len(mv), remaining_samples * 2)
        usable_bytes -= usable_bytes % 2
        if usable_bytes <= 0:
            return

        used_samples = usable_bytes // 2
        view = mv[:usable_bytes]
        sum_squares = state.probe_sum_squares
        peak = state.probe_peak
        for (sample,) in struct.iter_unpack("<h", view):
            normalized = float(sample) / 32768.0
            sum_squares += normalized * normalized
            abs_sample = abs(normalized)
            if abs_sample > peak:
                peak = abs_sample

        state.probe_sum_squares = sum_squares
        state.probe_peak = peak
        state.probe_samples_collected += used_samples

        if state.probe_samples_collected >= state.probe_target_samples:
            self._finalize_rms_probe(sid, state)

    def _finalize_rms_probe(self, sid: str, state: _SessionState) -> None:
        if not state.probe_active:
            return

        samples = state.probe_samples_collected
        if samples <= 0:
            state.probe_active = False
            state.probe_target_samples = 0
            state.probe_samples_collected = 0
            state.probe_sum_squares = 0.0
            state.probe_peak = 0.0
            return

        rms_avg = math.sqrt(state.probe_sum_squares / samples)
        rms_peak = state.probe_peak
        _log.info(
            "asr_probe rms_avg=%.6f rms_peak=%.6f sample_ms=%d",
            rms_avg,
            rms_peak,
            state.probe_window_ms,
        )

        state.probe_active = False
        state.probe_target_samples = 0
        state.probe_samples_collected = 0
        state.probe_sum_squares = 0.0
        state.probe_peak = 0.0

    def _forward_chunk(self, sid: str, state: _SessionState, chunk: bytes) -> None:
        if not chunk:
            return
        chunk_len = len(chunk)
        state.chunks_sent += 1
        state.bytes_sent += chunk_len
        stream_id = state.stream_id or state.last_stream_id or ""
        _log.info("evt=asr_chunk sid=%s stream_id=%s bytes=%d", sid, stream_id, chunk_len)
        self._ingest_rms_probe(sid, state, chunk)
        self._client.send_audio(sid, chunk)
        state.bytes_received += chunk_len

    def _make_partial_cb(self, sid: str) -> Callable[[str, Dict[str, object]], None]:
        def _callback(text: str, metadata: Dict[str, object] | None = None) -> None:
            self._on_partial(sid, text, metadata or {})

        return _callback

    def _make_final_cb(self, sid: str) -> Callable[[str, Dict[str, object]], None]:
        def _callback(text: str, metadata: Dict[str, object] | None = None) -> None:
            self._on_final(sid, text, metadata or {})

        return _callback

    def _make_error_cb(self, sid: str) -> Callable[[str, str | None], None]:
        def _callback(code: str, reason: str | None = None) -> None:
            error = code
            if reason and reason != code:
                error = f"{code}: {reason}"
            self._on_error(sid, error)

        return _callback

    def _make_close_cb(self, sid: str, state: _SessionState) -> Callable[[int | None, str | None], None]:
        def _callback(_code: int | None, _reason: str | None) -> None:
            stream_id = state.stream_id
            if not stream_id:
                return
            reason = state.close_reason
            if reason is None:
                reason = "server_shutdown" if _code == 1001 else "error"
            state.last_stream_id = stream_id
            _log.info(
                "evt=asr_session_close sid=%s stream_id=%s reason=%s",
                sid,
                stream_id,
                reason,
            )
            if (
                not state.unavailable_emitted
                and (
                    _code == 1011
                    or (_reason and "net0001" in str(_reason).lower())
                )
            ):
                detail = "Deepgram closed stream"
                if _code is not None:
                    detail += f" code={_code}"
                if _reason:
                    detail += f" reason={_reason}"
                self._publish_asr_unavailable(
                    sid,
                    "upstream_closed",
                    detail,
                    state=state,
                )
            state.stream_id = None
            state.close_reason = None
            state.stream_open = False
            state.req_id = None
            state.bytes_received = 0

        return _callback

    def _on_partial(
        self, sid: str, text: str, metadata: Dict[str, object] | None = None
    ) -> None:
        if not isinstance(text, str) or not text.strip():
            return
        state = self._sessions.get(sid)
        if state is None:
            state = _SessionState(sid=sid)
            self._sessions[sid] = state

        metadata = metadata or {}
        self._cancel_commit_timer(state)
        if state.stream_id is None:
            meta_stream = metadata.get("stream_id")
            if isinstance(meta_stream, str) and meta_stream:
                state.stream_id = meta_stream
        stream_id = state.stream_id or ""
        if not stream_id:
            stream_id = f"dg-stream-{uuid.uuid4().hex}"
            state.stream_id = stream_id
        state.last_stream_id = stream_id
        state.dg_msgs_partial += 1
        raw_len = metadata.get("len_chars")
        len_chars = len(text)
        if raw_len:
            try:
                candidate_len = int(raw_len)
            except (TypeError, ValueError):
                candidate_len = None
            if candidate_len is not None and candidate_len >= 0:
                len_chars = candidate_len
        utterance_id = metadata.get("utterance_id")
        if not utterance_id:
            utterance_id = f"dg-utt-{uuid.uuid4().hex}"
        latency_ms = int(metadata.get("latency_ms") or 0)

        now_monotonic = time.monotonic()
        should_log_partial = False
        if not state.first_partial_logged:
            should_log_partial = True
        elif now_monotonic - state.last_partial_log >= 0.5:
            should_log_partial = True
        if should_log_partial:
            state.first_partial_logged = True
            state.last_partial_log = now_monotonic
            _log.info(
                "asr_partial vendor=%s chars=%d latency_ms=%d",
                self._vendor,
                len_chars,
                latency_ms,
            )

        _log.debug(
            "evt=dg_partial sid=%s stream_id=%s len_chars=%d is_final=false "
            "utterance_id=%s latency_ms=%d",
            sid,
            stream_id,
            len_chars,
            utterance_id,
            latency_ms,
        )

        ensure_session = getattr(self._engine, "_ensure_session", None)
        engine_session = ensure_session(sid) if callable(ensure_session) else None
        engine_req_id = None
        if engine_session is not None:
            candidate = getattr(engine_session, "req_id", None)
            if isinstance(candidate, str) and candidate:
                engine_req_id = candidate

        if isinstance(engine_req_id, str) and engine_req_id:
            req_id = engine_req_id
            state.req_id = req_id
        else:
            req_id = state.req_id
            if not isinstance(req_id, str) or not req_id:
                req_id = f"dg-{uuid.uuid4().hex}"
                state.req_id = req_id
            if engine_session is not None and getattr(engine_session, "req_id", None) != req_id:
                engine_session.req_id = req_id

        try:
            self._engine.on_asr_partial(sid, req_id, _PARTIAL_CONFIDENCE, text)
        except Exception:  # pragma: no cover - defensive
            _log.exception("evt=asr_engine_partial_failed sid=%s", sid)

        event = {
            "type": EVT_ASR_PARTIAL,
            "sid": sid,
            "req_id": req_id,
            "text": text,
            "confidence": _PARTIAL_CONFIDENCE,
            "vendor": self._vendor,
        }
        self._bus.publish(event)

    def _on_final(
        self, sid: str, text: str, metadata: Dict[str, object] | None = None
    ) -> None:
        if not isinstance(text, str) or not text.strip():
            return
        state = self._sessions.get(sid)
        if state is None:
            state = _SessionState(sid=sid)
            self._sessions[sid] = state

        metadata = metadata or {}
        if state.stream_id is None:
            meta_stream = metadata.get("stream_id")
            if isinstance(meta_stream, str) and meta_stream:
                state.stream_id = meta_stream
        stream_id = state.stream_id or ""
        if not stream_id:
            stream_id = f"dg-stream-{uuid.uuid4().hex}"
            state.stream_id = stream_id
        state.last_stream_id = stream_id
        state.dg_msgs_final += 1
        raw_len = metadata.get("len_chars")
        len_chars = len(text)
        if raw_len:
            try:
                candidate_len = int(raw_len)
            except (TypeError, ValueError):
                candidate_len = None
            if candidate_len is not None and candidate_len >= 0:
                len_chars = candidate_len
        utterance_id = metadata.get("utterance_id")
        if not utterance_id:
            utterance_id = f"dg-utt-{uuid.uuid4().hex}"
        latency_ms = int(metadata.get("latency_ms") or 0)

        _log.info(
            "asr_final vendor=%s chars=%d latency_ms=%d",
            self._vendor,
            len_chars,
            latency_ms,
        )

        ensure_session = getattr(self._engine, "_ensure_session", None)
        engine_session = ensure_session(sid) if callable(ensure_session) else None
        engine_req_id = None
        if engine_session is not None:
            candidate = getattr(engine_session, "req_id", None)
            if isinstance(candidate, str) and candidate:
                engine_req_id = candidate

        if isinstance(engine_req_id, str) and engine_req_id:
            req_id = engine_req_id
        elif isinstance(state.req_id, str) and state.req_id:
            req_id = state.req_id
            if engine_session is not None and getattr(engine_session, "req_id", None) != req_id:
                engine_session.req_id = req_id
        else:
            req_id = f"req-{uuid.uuid4().hex}"
            if engine_session is not None:
                engine_session.req_id = req_id

        state.req_id = None

        _log.debug(
            "evt=dg_final sid=%s stream_id=%s len_chars=%d is_final=true "
            "utterance_id=%s latency_ms=%d req_id=%s",
            sid,
            stream_id,
            len_chars,
            utterance_id,
            latency_ms,
            req_id,
        )

        try:
            self._engine.on_asr_final(sid, text, req_id)
        except Exception:  # pragma: no cover - defensive
            _log.exception("evt=asr_engine_final_failed sid=%s", sid)

        event = {
            "type": EVT_ASR_FINAL,
            "sid": sid,
            "req_id": req_id,
            "text": text,
            "confidence": _FINAL_CONFIDENCE,
            "vendor": self._vendor,
        }
        self._bus.publish(event)
        state.finals_delivered += 1
        self._log_utterance(state, stream_id, req_id, metadata, text)

    def _on_error(self, sid: str, error: str) -> None:
        reason = error or "unknown"
        _log.error("asr_error vendor=%s code=stream reason=%s", self._vendor, reason)

        state = self._sessions.get(sid)
        if state is None:
            return
        self._publish_asr_unavailable(
            sid,
            "upstream_closed",
            error or "unknown error",
            state=state,
        )
        state.close_reason = "error"
        state.stream_open = False
        state.req_id = None
        self._cancel_idle_timer(state)
        self._cancel_ready_watchdog(state)
        self._cancel_commit_timer(state)
        state.utterance_active = False
        try:
            self._client.close_stream(sid)
        except Exception:  # pragma: no cover - defensive
            _log.exception("evt=asr_error_close_failed sid=%s", sid)
        finally:
            self._finalize_rms_probe(sid, state)

    def _publish_asr_unavailable(
        self,
        sid: str,
        reason: str,
        details: str,
        *,
        state: _SessionState | None = None,
        force: bool = False,
    ) -> None:
        if not isinstance(sid, str) or not sid:
            return
        if not isinstance(reason, str) or not reason:
            return
        if state is None:
            state = self._sessions.get(sid)
        if state is not None and state.unavailable_emitted and not force:
            return

        details_value = details if isinstance(details, str) else str(details)
        if len(details_value) > 160:
            details_value = f"{details_value[:157]}..."
        payload = {
            "type": "asr.unavailable",
            "sid": sid,
            "reason": reason,
            "details": details_value,
        }
        try:
            self._bus.publish(payload)
        except Exception:  # pragma: no cover - defensive
            _log.exception("evt=asr_unavailable_publish_failed sid=%s", sid)

        level = "WARNING" if reason == "no_audio_timeout" else "ERROR"
        self._emit_log(
            {
                "type": "EVT_LOG",
                "logger": "app.voice_v2.asr_runtime",
                "level": level,
                "sid": sid,
                "msg": (
                    f"evt=asr_unavailable sid={sid} reason={reason} details={details_value}"
                ),
            }
        )

        if state is not None:
            state.unavailable_emitted = True

    def _log_asr_rearmed(self, sid: str, state: _SessionState | None = None) -> None:
        if not isinstance(sid, str) or not sid:
            return
        if state is None:
            state = self._sessions.get(sid)
        if state is None or not state.unavailable_emitted:
            return

        self._emit_log(
            {
                "type": "EVT_LOG",
                "logger": "app.voice_v2.asr_runtime",
                "level": "INFO",
                "sid": sid,
                "msg": f"evt=asr_rearmed sid={sid}",
            }
        )
        state.unavailable_emitted = False

    def _cancel_idle_timer(self, state: _SessionState) -> None:
        handle = state.idle_handle
        if handle is not None:
            handle.cancel()
            state.idle_handle = None

    def _cancel_ready_watchdog(self, state: _SessionState) -> None:
        handle = state.ready_watchdog
        if handle is not None:
            handle.cancel()
        state.ready_watchdog = None
        state.ready_armed_at = 0.0

    def _start_ready_watchdog(self, sid: str, state: _SessionState) -> None:
        loop = self._ensure_loop()
        if loop is None:
            return
        self._cancel_ready_watchdog(state)

        def _fire() -> None:
            state.ready_watchdog = None
            armed_at = state.ready_armed_at
            state.ready_armed_at = 0.0
            if armed_at and state.last_audio_ts <= armed_at:
                _log.warning("evt=asr_no_audio_timeout sid=%s", sid)
                self._publish_asr_unavailable(
                    sid,
                    "no_audio_timeout",
                    f"no audio received for {int(_NO_AUDIO_TIMEOUT_S)}s",
                    state=state,
                    force=False,
                )
                self._bus.publish({"type": EVT_ASR_READY, "sid": sid, "vendor": self._vendor})
                _log.info("evt=asr_ready_published sid=%s vendor=%s", sid, self._vendor)
                self._log_asr_rearmed(sid, state)
                asr_ready_frame = {
                    "type": "asr.ready",
                    "vendor": self._vendor,
                    "input": dict(state.input_desc),
                }
                self._bus.publish(
                    {
                        "type": EVT_WS_JSON_SEND,
                        "sid": sid,
                        "frame": asr_ready_frame,
                        "payload": asr_ready_frame,
                    }
                )

        state.ready_armed_at = time.monotonic()
        state.ready_watchdog = loop.call_later(_NO_AUDIO_TIMEOUT_S, _fire)

    def _reset_idle_timer(
        self, sid: str, state: _SessionState, *, delay_override: float | None = None
    ) -> None:
        threshold_ms = self._idle_close_ms
        if delay_override is None and threshold_ms <= 0:
            return
        loop = self._ensure_loop()
        if loop is None:
            return
        self._cancel_idle_timer(state)

        delay = delay_override if delay_override is not None else threshold_ms / 1000.0
        if delay <= 0:
            return

        def _fire() -> None:
            state.idle_handle = None
            self._handle_idle_timeout(sid, state)

        state.idle_handle = loop.call_later(delay, _fire)

    def _handle_idle_timeout(self, sid: str, state: _SessionState) -> None:
        if not state.stream_open:
            return

        now_monotonic = time.monotonic()
        if state.keep_warm_until and now_monotonic < state.keep_warm_until:
            remaining = state.keep_warm_until - now_monotonic
            self._reset_idle_timer(sid, state, delay_override=remaining)
            return

        state.close_reason = "idle_timeout"
        ensure_session = getattr(self._engine, "_ensure_session", None)
        engine_state = None
        if callable(ensure_session):
            try:
                engine_session = ensure_session(sid)
            except Exception:  # pragma: no cover - defensive
                engine_session = None
            if engine_session is not None:
                engine_state = getattr(engine_session, "state", None)

        close_for_prearm = False
        if engine_state == _LISTENING_STATE:
            if state.chunks_sent == 0 and not state.pending:
                close_for_prearm = True
            else:
                self._reset_idle_timer(sid, state)
                return
        elif engine_state not in _READY_STATES:
            # Re-arm the timer to check again once the engine transitions.
            self._reset_idle_timer(sid, state)
            return

        idle_ms = int(
            max(0.0, (time.monotonic() - state.last_audio_ts) * 1000)
            if state.last_audio_ts
            else self._idle_close_ms
        )
        stream_id = state.stream_id or state.last_stream_id or ""
        state.keep_warm_until = 0.0
        if close_for_prearm:
            _log.warning(
                "evt=asr_prearm_idle sid=%s stream_id=%s idle_ms=%d action=close",
                sid,
                stream_id,
                idle_ms,
            )
        else:
            _log.info(
                "evt=asr_idle_close sid=%s stream_id=%s idle_ms=%d",
                sid,
                stream_id,
                idle_ms,
            )
        try:
            self._client.close_stream(sid)
        except Exception:  # pragma: no cover - defensive
            _log.exception("evt=asr_idle_close_failed sid=%s", sid)
        finally:
            self._finalize_rms_probe(sid, state)
        self._cancel_commit_timer(state)
        state.stream_open = False
        state.last_stream_id = stream_id or state.last_stream_id
        state.stream_id = None
        state.req_id = None
        state.bytes_received = 0
        state.utterance_active = False

    def commit(self, sid: str, *, reason: str | None = None) -> None:
        if not isinstance(sid, str) or not sid:
            return
        state = self._sessions.get(sid)
        if state is None or not state.stream_open:
            return
        state.close_reason = reason or "commit"
        _log.info(
            "evt=asr_commit sid=%s vendor=%s reason=%s",
            sid,
            self._vendor,
            state.close_reason,
        )
        self._emit_log(
            {
                "type": "EVT_LOG",
                "logger": "app.voice_v2.asr_runtime",
                "level": "INFO",
                "sid": sid,
                "msg": (
                    "evt=asr_commit sid=%s vendor=%s reason=%s"
                    % (sid, self._vendor, state.close_reason)
                ),
            }
        )
        self._cancel_idle_timer(state)
        self._cancel_commit_timer(state)
        state.utterance_active = False
        try:
            commit_method = getattr(self._client, "commit_stream", None)
            if callable(commit_method):
                commit_method(sid)
            else:
                self._client.close_stream(sid)
        except Exception:  # pragma: no cover - defensive
            _log.exception("evt=asr_commit_close_failed sid=%s vendor=%s", sid, self._vendor)
        finally:
            self._finalize_rms_probe(sid, state)
        state.stream_open = False
        state.stream_id = None
        state.req_id = None
        state.bytes_received = 0
        state.buffered_bytes = 0
        state.pending.clear()

    # Optional idle guard
    def close_idle_streams(self) -> None:
        if self._loop is None:
            return
        now = time.monotonic()
        threshold = self._idle_close_ms / 1000.0
        for sid, state in list(self._sessions.items()):
            if not state.stream_open:
                continue
            if threshold <= 0:
                continue
            if state.last_audio_ts and now - state.last_audio_ts >= threshold:
                state.close_reason = "idle_timeout"
                try:
                    stream_id = state.stream_id or state.last_stream_id or ""
                    idle_ms = int(max(0.0, (now - state.last_audio_ts) * 1000))
                    _log.info(
                        "evt=asr_idle_close sid=%s stream_id=%s idle_ms=%d",
                        sid,
                        stream_id,
                        idle_ms,
                    )
                    self._client.close_stream(sid)
                except Exception:  # pragma: no cover - defensive
                    _log.exception("evt=asr_idle_close_failed sid=%s", sid)
                finally:
                    self._finalize_rms_probe(sid, state)
                self._cancel_commit_timer(state)
                state.stream_open = False
                state.last_stream_id = state.stream_id or state.last_stream_id
                state.stream_id = None
                state.req_id = None
                state.bytes_received = 0
                state.utterance_active = False

    def _apply_commit_policy(self, state: _SessionState) -> None:
        commit_on_vad, commit_silence_ms, max_utterance_ms = self._resolve_commit_policy()
        state.commit_on_vad_silence = commit_on_vad
        state.commit_silence_ms = commit_silence_ms
        state.max_utterance_ms = max_utterance_ms

    def _resolve_commit_policy(self) -> tuple[bool, int, int]:
        asr_policy = getattr(self.policy, "asr", None)
        commit_on_vad = True
        commit_silence_ms = 900
        max_utterance_ms = 8000
        if asr_policy is not None:
            commit_on_vad = bool(
                getattr(asr_policy, "commit_on_vad_silence", commit_on_vad)
            )
            commit_silence_ms = self._coerce_policy_int(
                getattr(asr_policy, "commit_silence_ms", commit_silence_ms),
                commit_silence_ms,
            )
            max_utterance_ms = self._coerce_policy_int(
                getattr(asr_policy, "max_utterance_ms", max_utterance_ms),
                max_utterance_ms,
            )

        snapshot = getattr(self._engine, "policy_snapshot", None)
        if isinstance(snapshot, Mapping):
            policy_block = snapshot.get("policy")
            if isinstance(policy_block, Mapping):
                asr_block = policy_block.get("asr")
                if isinstance(asr_block, Mapping):
                    if "commit_on_vad_silence" in asr_block:
                        commit_on_vad = bool(asr_block.get("commit_on_vad_silence"))
                    if "commit_silence_ms" in asr_block:
                        commit_silence_ms = self._coerce_policy_int(
                            asr_block.get("commit_silence_ms"), commit_silence_ms
                        )
                    if "max_utterance_ms" in asr_block:
                        max_utterance_ms = self._coerce_policy_int(
                            asr_block.get("max_utterance_ms"), max_utterance_ms
                        )
        return commit_on_vad, commit_silence_ms, max_utterance_ms

    @staticmethod
    def _coerce_policy_int(value: Any, default: int) -> int:
        try:
            candidate = int(value)
        except (TypeError, ValueError):
            return max(0, int(default))
        return max(0, candidate)

    def _start_commit_timer(self, sid: str, state: _SessionState) -> None:
        if state.max_utterance_ms <= 0 or state.commit_timer is not None:
            return
        loop = self._ensure_loop()
        if loop is None:
            return
        delay = state.max_utterance_ms / 1000.0
        if delay <= 0:
            return

        def _fire() -> None:
            state.commit_timer = None
            _log.info(
                "evt=asr_commit_timer_fired sid=%s vendor=%s max_utterance_ms=%d",
                sid,
                self._vendor,
                state.max_utterance_ms,
            )
            self.commit(sid, reason="time_cap")

        state.commit_timer = loop.call_later(delay, _fire)
        state.utterance_started_at = time.monotonic()

    @staticmethod
    def _cancel_commit_timer(state: _SessionState) -> None:
        handle = state.commit_timer
        if handle is not None:
            handle.cancel()
        state.commit_timer = None
        state.utterance_started_at = 0.0
