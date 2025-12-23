"""Minimal Engine v2 shell with telemetry hooks and session exporting."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import inspect
import json
import logging
import re
import threading
import time
import uuid
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence

from app import config
from app.logging_config import apply_logging_policy
from app.policy.loader import assistant_turn_actions, load_interaction_policy
from app.policy.watch import compute_diff, should_reapply
from app.telemetry import bus
from app.telemetry.exporter import FileExporter
from app.voice_v2 import (
    EVT_ACWR_RECOMPUTE,
    EVT_CHAT_USER,
    EVT_ASR_FINAL,
    EVT_ASR_PARTIAL,
    EVT_ACTION_SAY_END,
    EVT_BARGE_CONFIRMED,
    EVT_BARGE_DETECTED,
    EVT_BARGE_REJECTED,
    EVT_NLG,
    EVT_NLU,
    EVT_DIALOG_PLAN,
    EVT_POLICY_APPLIED,
    EVT_TTS_END,
    EVT_TTS_START,
    EVT_TTS_MASK,
    EVT_WS_AUDIO_RECV,
    EVT_WS_AUDIO_SEND,
    EVT_WS_CLOSE,
    EVT_WS_JSON_RECV,
    EVT_WS_JSON_SEND,
    EVT_WS_OPEN,
    EVT_CLIENT_MIC_OPEN,
)
from app.voice_v2.nlu import NLUAdapter
from app.voice_v2.policy_decider import PolicyDecider, EVT_POLICY_DECISION
from app.voice_v2.llm import LLMAdapter
from app.voice_v2.gate import GateController
from app.voice_v2.conversation_buffer import ConversationBuffer
from app.voice_v2.vad import VADAggregator, rms_dbfs_from_pcm16
from app.voice_v2.planner import _MODE_CHIPS, plan_turn
from app.voice_v2.persona import default_chips_for_mode, load_persona
from app.voice_v2.streaming import StreamingController
from app.voice_v2.rollup import TurnRollupAggregator


_log = logging.getLogger(__name__)


def _now_ms() -> int:
    """Return the current epoch timestamp in milliseconds."""
    return int(time.time() * 1000)


def _slugify_action_label(label: str) -> str:
    """Normalize a suggestion label into a stable slug identifier."""

    normalized = re.sub(r"[^a-z0-9]+", "-", label.strip().lower())
    slug = normalized.strip("-")
    return slug or "action"


READY = "Ready"
LISTENING = "Listening"
THINKING = "Thinking"
RESPONDING = "Responding"
CONFIRMING_BARGE = "ConfirmingBarge"

_SENTENCE_CHUNK_RE = re.compile(r"[^.?!…]+(?:[.?!…]+|\Z)", re.DOTALL)


def _iter_sentence_chunks(text: str) -> Iterable[str]:
    """Yield sentence-like chunks while preserving whitespace."""

    if not isinstance(text, str) or not text:
        return
    for match in _SENTENCE_CHUNK_RE.finditer(text):
        chunk = match.group(0)
        if chunk:
            yield chunk

_STATE_ORDER = {
    READY: 0,
    LISTENING: 1,
    THINKING: 2,
    RESPONDING: 3,
    CONFIRMING_BARGE: 4,
}

_ALLOWED_TRANSITIONS = {
    READY: {LISTENING, RESPONDING},
    LISTENING: {THINKING},
    THINKING: {RESPONDING},
    RESPONDING: {READY, CONFIRMING_BARGE, LISTENING},
    CONFIRMING_BARGE: {READY, LISTENING},
}

_PCM_CODEC = "pcm_s16le"
_PCM_RATE_HZ = 16000
_PCM_CHANNELS = 1
_PCM_DESCRIPTOR = {
    "codec": _PCM_CODEC,
    "rate_hz": _PCM_RATE_HZ,
    "channels": _PCM_CHANNELS,
}

_CONCISE_MAX_TOKENS = 48

_DEFAULT_VOICE_ID = "alloy-en-US-001"
_DEFAULT_LOCALE = "en-US"

_INFO_SLO = {
    "first_partial_ms": {"target": 450, "p95": 750},
    "final_ms": {"target": 2000, "p95": 3000},
    "tts_start_ms": {"target": 350, "p95": 600},
}
_PCM_SAMPLE_BYTES = 2 * _PCM_CHANNELS

EVT_TURN_STATE = "EVT_TURN_STATE"
EVT_TURN_BEGIN = "EVT_TURN_BEGIN"
EVT_TURN_END = "EVT_TURN_END"
EVT_PERF_SUMMARY = "EVT_PERF_SUMMARY"
EVT_LLM_RESPONSE_START = "EVT_LLM_RESPONSE_START"
EVT_LLM_RESPONSE_END = "EVT_LLM_RESPONSE_END"


@dataclass
class _Envelope:
    """Normalized telemetry envelope returned by the engine hooks."""

    type: str
    sid: str
    payload: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        data = {"type": self.type, "sid": self.sid}
        data.update(self.payload)
        if "meta" in data and isinstance(data["meta"], Mapping):
            data["meta"] = dict(data["meta"])
        if "ts_ms" not in data or not isinstance(data["ts_ms"], int):
            data["ts_ms"] = _now_ms()
        data.setdefault("who", "server")
        data.setdefault("source", "voice_engine")
        data.setdefault("level", "debug")
        return data


@dataclass
class _TurnSession:
    state: str = READY
    turn_id: Optional[str] = None
    turn_started_ms: Optional[int] = None
    tts_utt_id: Optional[str] = None
    tts_mask_phase: str = "off"
    req_id: Optional[str] = None
    last_client_msg_id: Optional[str] = None
    nlu_req_id: Optional[str] = None
    perf_first_partial_ms: Optional[int] = None
    perf_final_ms: Optional[int] = None
    perf_tts_start_ms: Optional[int] = None
    asr_final_emitted: bool = False
    nlu_emitted: bool = False
    policy_emitted: bool = False
    nlg_emitted: bool = False
    plan_emitted: bool = False
    greet_emitted: bool = False
    plan: Optional[Dict[str, Any]] = None
    suggestions_emitted: bool = False
    turn_committed: bool = False
    policy_actions_mismatch_logged: bool = False
    adaptive_utterance_end_ms: Optional[int] = None
    adaptive_commit_silence_ms: Optional[int] = None
    adaptive_extended_once: bool = False
    assistant_turn_open: bool = False
    history_message_count: int = 0
    answer_chars: Optional[int] = None
    metrics_asr_final_ms: Optional[int] = None
    metrics_llm_start_ms: Optional[int] = None
    metrics_llm_end_ms: Optional[int] = None
    metrics_tts_start_ms: Optional[int] = None
    metrics_tts_first_chunk_ms: Optional[int] = None
    metrics_tts_end_ms: Optional[int] = None
    metrics_logged_stages: set[str] = field(default_factory=set)


class _NullExporter:
    """Exporter stub used when no exporter is provided."""

    def write(self, sid: str, event: Dict[str, Any]) -> None:  # pragma: no cover - noop
        return


class EngineV2:
    """Engine shell that exposes WS hooks, telemetry taps, and exporting."""

    def __init__(
        self,
        exporter: FileExporter | None = None,
        *,
        telemetry_bus=bus,
        fake_exporter: FileExporter | None = None,
    ) -> None:
        if exporter is None:
            exporter = fake_exporter
        if exporter is None:
            exporter = _NullExporter()
        self._exporter = exporter
        self._bus = telemetry_bus
        self._policy_snapshot: Dict[str, Any] | None = None
        self._last_sid: Optional[str] = None
        self._gate = GateController(publish=self._publish_gate_event)
        self._sessions: Dict[str, _TurnSession] = {}
        self._barge_handles: Dict[str, object] = {}
        self._barge_attempts: Dict[str, Dict[str, Any]] = {}
        self._aggregators: Dict[str, VADAggregator] = {}
        self._streaming = StreamingController()
        self._conversation_buffer = ConversationBuffer()
        self._turn_rollup = TurnRollupAggregator(self._bus)
        self._nlu = NLUAdapter()
        self._policy_decider = PolicyDecider()
        self._llm = LLMAdapter(telemetry_bus=self._bus, auto_publish=False)
        subscribe = getattr(self._bus, "subscribe", None)
        if callable(subscribe):
            self._chat_subscription = subscribe(EVT_CHAT_USER, self._handle_chat_user_event)
            self._ws_send_subscription = subscribe(
                EVT_WS_JSON_SEND, self._handle_outbound_chat_frame
            )
        else:
            self._chat_subscription = None
            self._ws_send_subscription = bus.subscribe(
                EVT_WS_JSON_SEND, self._handle_outbound_chat_frame
            )

    @property
    def policy_snapshot(self) -> Dict[str, Any] | None:
        return self._policy_snapshot

    @policy_snapshot.setter
    def policy_snapshot(self, snapshot: Dict[str, Any] | None) -> None:
        self._policy_snapshot = dict(snapshot) if snapshot is not None else None

    def on_open(self, sid: str, headers: Mapping[str, str]) -> None:
        """Capture a successful WebSocket upgrade."""
        meta = {"headers": dict(headers), "dir": "in"}
        event = self._envelope(sid, EVT_WS_OPEN, {"meta": meta})
        self._publish(event)

        self._last_sid = sid
        self._policy_snapshot = None
        self._streaming.reset_session(sid)
        session = self._ensure_session(sid)
        self.reapply_policy()
        self._emit_info_frame(sid)
        if session.state != READY:
            self._set_state(sid, READY, reason="ws_open")

    async def start_greet(self, sid: str) -> None:
        """Begin the greeting flow asynchronously."""
        await asyncio.sleep(0)
        self._maybe_emit_greeting(sid)
        self._maybe_emit_connect_suggestions(sid)

    def on_json(self, sid: str, frame: Mapping[str, Any]) -> None:
        """Capture a validated JSON frame from the adapter."""
        turn_id: Optional[Any] = None
        meta: Dict[str, Any] = {"dir": "in"}
        frame_type: Optional[str] = None
        if isinstance(frame, Mapping):
            frame_type = frame.get("type")
            if isinstance(frame_type, str):
                meta["frame_type"] = frame_type
            turn_id = frame.get("turn_id")
        else:
            frame = {}
        try:
            serialized = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            serialized = "{}"
        meta["byte_count"] = len(serialized.encode("utf-8"))
        payload: Dict[str, Any] = {"meta": meta}
        if turn_id is not None:
            payload["turn_id"] = turn_id
        event = self._envelope(sid, EVT_WS_JSON_RECV, payload)
        self._publish(event)
        if frame_type == "client.diag":
            self._emit_client_diag(sid, frame)

    def _emit_client_diag(self, sid: str, frame: Mapping[str, Any]) -> None:
        if not config.DIAG_CLIENT_HUD:
            return
        if not isinstance(frame, Mapping):
            return

        meta: Dict[str, Any] = {"dir": "in"}

        event_name = frame.get("event")
        if isinstance(event_name, str) and event_name:
            meta["event"] = event_name[:64]

        message = frame.get("message")
        if isinstance(message, str) and message:
            meta["message"] = message[:256]

        sample_flag = frame.get("sample")
        if isinstance(sample_flag, bool):
            meta["sample"] = sample_flag

        badge = frame.get("badge")
        if isinstance(badge, str) and badge:
            meta["badge"] = badge[:64]

        client_ts = frame.get("ts")
        if isinstance(client_ts, int):
            meta["client_ts"] = client_ts

        data = frame.get("data")
        if data is not None:
            try:
                meta["data"] = bus.redact_payload(data)
            except Exception:
                try:
                    meta["data"] = bus.redact_payload(str(data))
                except Exception:
                    meta["data"] = str(data)

        diag_event = self._envelope(sid, "EVT_DIAG_HUD", {"meta": meta})

        level = frame.get("level")
        if isinstance(level, str) and level:
            diag_event["level"] = level[:16]

        diag_event["who"] = "client"
        diag_event["source"] = "client_hud"

        self._publish(diag_event)

    def _handle_chat_user_event(self, event: Dict[str, Any]) -> None:
        if event.get("type") != EVT_CHAT_USER:
            return
        sid = event.get("sid")
        if not isinstance(sid, str) or not sid:
            return
        text = event.get("text")
        if not isinstance(text, str):
            return
        client_msg_id = event.get("client_msg_id")
        if client_msg_id is not None and not isinstance(client_msg_id, str):
            client_msg_id = None

        session = self._ensure_session(sid)
        # Drop duplicate chat.user events if the client re-submits the same client_msg_id
        if isinstance(client_msg_id, str) and client_msg_id and session.last_client_msg_id == client_msg_id:
            _log.info(
                "evt=chat_user_dedup sid=%s client_msg_id=%s",
                sid,
                client_msg_id,
                extra={"sid": sid, "event": "chat_user_dedup"},
            )
            return
        session.last_client_msg_id = client_msg_id if isinstance(client_msg_id, str) else None
        previous_state = session.state
        was_responding = previous_state == RESPONDING
        policy = self.policy_snapshot or {}
        barge_enabled = bool(policy.get("barge_in_enabled"))

        if previous_state == RESPONDING:
            granted = barge_enabled
            reason = None if granted else "policy_disabled"
            self._publish_barge_event(
                sid,
                "text",
                event_type=EVT_BARGE_DETECTED,
                granted=granted,
                reason=reason,
            )
            if granted:
                self.cancel_current_tts(sid, reason="canceled")
                self._publish_barge_event(
                    sid,
                    "text",
                    event_type=EVT_BARGE_CONFIRMED,
                    granted=True,
                )
            else:
                reject_reason = reason or "policy_disabled"
                self._publish_barge_event(
                    sid,
                    "text",
                    event_type=EVT_BARGE_REJECTED,
                    granted=False,
                    reason=reject_reason,
                )
            self._set_state(
                sid,
                READY,
                reason="text_barge_reset" if granted else "text_input_reset",
            )
            session = self._ensure_session(sid)
            previous_state = session.state

        if previous_state != READY:
            _log.warning(
                "evt=text_input_ignored state=%s", previous_state, extra={"sid": sid}
            )
            return

        listen_reason = "text_barge" if (was_responding and barge_enabled) else "text_input"
        self._set_state(sid, LISTENING, reason=listen_reason)
        session = self._ensure_session(sid)
        if session.state != LISTENING:
            return

        self._commit_turn_start(sid, "text_input")
        session = self._ensure_session(sid)

        session = self._ensure_session(sid)
        turn_id = session.turn_id or str(uuid.uuid4())
        req_id = session.req_id or f"req-{uuid.uuid4().hex}"
        session.turn_id = turn_id
        session.req_id = req_id

        self._record_turn_timing(sid, session, "asr_final")

        self._emit_user_chat_message(sid, text, turn_id, req_id, client_msg_id)

        # --- OMNI-CHANNEL FIX START: Trigger NLU/LLM pipeline for text input ---
        nlu_result = self._nlu.extract(req_id, text)
        nlu_payload = {"req_id": req_id, "turn_id": turn_id, **nlu_result}

        nlu_event = self._envelope(sid, EVT_NLU, nlu_payload)
        self._publish(nlu_event)

        session.nlu_emitted = True
        session.nlu_req_id = req_id

        self._emit_dialog_plan(sid, session, req_id, turn_id, text)
        self._maybe_emit_policy_and_nlg(sid, session, nlu_payload)
        # --- OMNI-CHANNEL FIX END ---
        
        self._set_state(sid, THINKING, reason="text_input")

    def on_audio(self, sid: str, chunk: bytes, seq: int) -> None:
        """Capture an incoming audio chunk."""
        session = self._ensure_session(sid)
        if session.tts_mask_phase != "off":
            self._publish_tts_mask(sid, "off")
        if session.state != LISTENING:
            _log.warning(
                "evt=audio_ignored state=%s", session.state, extra={"sid": sid}
            )
        else:
            self._commit_turn_start(sid, "audio_rx")

        if not isinstance(chunk, (bytes, bytearray)):
            _log.warning(
                "evt=audio_invalid_chunk type=%s", type(chunk).__name__, extra={"sid": sid}
            )
            return

        byte_count = len(chunk)
        if byte_count % _PCM_SAMPLE_BYTES != 0:
            _log.warning(
                "evt=audio_invalid_chunk_size bytes=%d", byte_count, extra={"sid": sid}
            )
            return
        frame_ms = int((byte_count / (2 * _PCM_CHANNELS * _PCM_RATE_HZ)) * 1000)
        if frame_ms <= 0:
            frame_ms = 20
        dbfs = rms_dbfs_from_pcm16(chunk)
        aggregator = self._aggregators.get(sid)
        if aggregator is not None:
            aggregator.feed_auto_energy(dbfs, frame_ms=frame_ms)

        if session.perf_first_partial_ms is None and not getattr(
            session, "_vad_energy_logged", False
        ):
            _log.info(
                "evt=auto_vad_energy sid=%s dbfs=%.1f frame_ms=%d",
                sid,
                dbfs,
                frame_ms,
                extra={"sid": sid, "event": "auto_vad_energy"},
            )
            session._vad_energy_logged = True
        meta = {"dir": "in", "byte_count": byte_count, "seq": seq}
        event = self._envelope(sid, EVT_WS_AUDIO_RECV, {"meta": meta})
        self._publish(event)

    def on_close(self, sid: str, code: int, reason: Optional[str]) -> None:
        """Capture the WebSocket closing handshake."""
        meta = {"code": code, "reason": reason}
        event = self._envelope(sid, EVT_WS_CLOSE, {"meta": meta})
        self._publish(event)
        if self._last_sid == sid:
            self._last_sid = None
            self._policy_snapshot = None
        self.cancel_current_tts(sid, reason="ended")
        self._streaming.close_session(sid)
        self._gate.clear_all(sid=sid)
        self._cancel_barge_confirmation(sid, reject_reason="session_closed")
        self._aggregators.pop(sid, None)
        if hasattr(self, "_turn_rollup"):
            self._turn_rollup.clear_session(sid)
        self._sessions.pop(sid, None)
        buffers = getattr(self._conversation_buffer, "_buffers", None)
        if isinstance(buffers, dict):
            buffers.pop(sid, None)

    def on_tts_start(
        self,
        sid: str,
        utt_id: str,
        post_hold_ms: int | None = None,
        *,
        is_greet: bool = False,
    ) -> None:
        """Engage the TTS mask and emit a telemetry breadcrumb."""

        session = self._ensure_session(sid)
        session.tts_utt_id = utt_id
        if session.turn_started_ms is not None and session.perf_tts_start_ms is None:
            elapsed = _now_ms() - session.turn_started_ms
            if elapsed < 0:
                elapsed = 0
            session.perf_tts_start_ms = elapsed

        hold_ms = post_hold_ms or 0
        voice_id, locale = self._voice_profile()
        tts_meta: Dict[str, Any] = {
            "tts": {"utt_id": utt_id, "post_hold_ms": hold_ms},
            "voice_id": voice_id,
            "locale": locale,
        }
        if is_greet:
            tts_meta["is_greet"] = True
            tts_meta["tts"]["is_greet"] = True

        _log.info(
            "TURN_METRICS stage=tts_start sid=%s utt_id=%s is_greet=%s", sid, utt_id, is_greet
        )
        self._gate.set_reason(
            "tts_active",
            True,
            sid=sid,
            meta=tts_meta,
        )

        self._publish_tts_mask(sid, "on")

        payload = {"meta": tts_meta}
        req_id = session.req_id
        if isinstance(req_id, str) and req_id:
            payload["req_id"] = req_id
        event = self._envelope(sid, EVT_TTS_START, payload)
        self._publish(event)
        self._record_turn_timing(sid, session, "tts_start")
        self._set_state(sid, RESPONDING, reason="tts_start")

        aggregator = self._aggregators.get(sid)
        if aggregator is not None:
            aggregator.on_tts_start()

        self._streaming.set_output_finalizer(
            sid, lambda _sid=sid: self.cancel_current_tts(_sid, reason="ended")
        )

    def on_tts_end(
        self,
        sid: str,
        utt_id: str,
        post_hold_ms: int | None = None,
        *,
        is_greet: bool = False,
    ) -> None:
        """Release the TTS mask and optionally engage a post-hold."""

        hold_ms = post_hold_ms or 0
        self._teardown_tts(
            sid,
            utt_id,
            post_hold_ms=hold_ms,
            transition_to_ready=True,
            is_greet=is_greet,
        )

    def emit_tts_audio_chunk(self, sid: str, chunk: bytes | bytearray | memoryview) -> None:
        """Emit a PCM chunk for server-to-client playback."""

        if not isinstance(sid, str) or not sid:
            raise ValueError("sid must be a non-empty string")

        session = self._ensure_session(sid)

        if isinstance(chunk, (bytes, bytearray, memoryview)):
            pcm_chunk = bytes(chunk)
        else:  # pragma: no cover - defensive guard
            raise TypeError("chunk must be bytes-like")

        if not pcm_chunk:
            return

        self._record_turn_timing(sid, session, "tts_first_chunk")

        byte_count = len(pcm_chunk)
        if byte_count % _PCM_SAMPLE_BYTES != 0:
            raise ValueError("PCM chunk must align to 16-bit mono frames")

        meta = {
            "byte_count": byte_count,
            "audio": dict(_PCM_DESCRIPTOR),
            "ws": {"dir": "out", "size": byte_count},
        }
        payload: Dict[str, Any] = {"meta": meta, "chunk": pcm_chunk}
        event = self._envelope(sid, EVT_WS_AUDIO_SEND, payload)
        self._publish(event)

    def on_asr_final(self, sid: str, text: str, req_id: str | None = None) -> None:
        """Observe the final ASR transcript for a turn."""

        _log.info(
            "evt=voice.on_asr_final sid=%s req_id=%s text_preview=%s",
            sid,
            req_id,
            (text[:80] + "…") if isinstance(text, str) and len(text) > 80 else text,
        )

        session = self._ensure_session(sid)

        if session.state != LISTENING:
            _log.warning(
                "evt=asr_final_ignored state=%s", session.state, extra={"sid": sid}
            )
            return

        self._commit_turn_start(sid, "asr_final")
        session = self._ensure_session(sid)

        provided_req_id = req_id if isinstance(req_id, str) and req_id else None

        active_req_id = session.req_id

        if not isinstance(active_req_id, str) or not active_req_id:
            active_req_id = provided_req_id or f"req-{uuid.uuid4().hex}"
            session.req_id = active_req_id
        elif provided_req_id is not None and provided_req_id != active_req_id:
            _log.debug(
                "evt=asr_final_req_id_mismatch sid=%s active_req_id=%s provided_req_id=%s (adopting_provided)",
                sid,
                active_req_id,
                provided_req_id,
            )
            active_req_id = provided_req_id
            session.req_id = active_req_id

        req_id_value = session.req_id
        if not isinstance(req_id_value, str) or not req_id_value:
            return

        turn_index = getattr(session, "turn_index", 0)
        if turn_index >= 1:
            nlu_payload: Dict[str, Any] = {
                "req_id": req_id_value,
                "turn_id": session.turn_id
                if isinstance(session.turn_id, str) and session.turn_id
                else req_id_value,
                "intent": "chitchat.fallback",
                "entities": {},
                "text": text,
            }

            _log.info(
                "evt=voice.policy_bridge_start sid=%s req_id=%s turn_index=%s",
                sid,
                req_id_value,
                turn_index,
            )

            try:
                self._apply_policy_decision(sid, nlu_payload)
            except Exception:
                _log.exception(
                    "evt=voice.policy_bridge_failed sid=%s req_id=%s", sid, req_id_value
            )

        if session.turn_started_ms is not None:
            elapsed = _now_ms() - session.turn_started_ms
            if elapsed < 0:
                elapsed = 0
            session.perf_final_ms = elapsed
        else:
            session.turn_started_ms = _now_ms()

        turn_id = session.turn_id or str(uuid.uuid4())
        session.turn_id = turn_id

        req_id = active_req_id

        self._record_turn_timing(sid, session, "asr_final")

        if session.nlu_req_id == req_id and session.nlu_emitted:
            _log.warning(
                "evt=nlu_duplicate_req_id sid=%s req_id=%s",
                sid,
                req_id,
                extra={"sid": sid, "req_id": req_id},
            )
            return

        if not session.asr_final_emitted:
            session.asr_final_emitted = True

            final_payload: Dict[str, Any] = {
                "req_id": req_id,
                "turn_id": turn_id,
                "text": text,
                "confidence": 0.9,
            }
            final_event = self._envelope(sid, EVT_ASR_FINAL, final_payload)
            self._publish(final_event)

            skip_nlu = session.nlu_req_id == req_id

            if skip_nlu:
                _log.warning(
                    "evt=nlu_duplicate_req_id sid=%s req_id=%s",
                    sid,
                    req_id,
                    extra={"sid": sid, "req_id": req_id},
                )
            elif not session.nlu_emitted:
                nlu_result = self._nlu.extract(req_id, text)
                nlu_payload = {"req_id": req_id, "turn_id": turn_id, **nlu_result}
                nlu_event = self._envelope(sid, EVT_NLU, nlu_payload)
                self._publish(nlu_event)
                session.nlu_emitted = True
                session.nlu_req_id = req_id
                self._emit_dialog_plan(sid, session, req_id, turn_id, text)
                self._maybe_emit_policy_and_nlg(sid, session, nlu_payload)

            self._emit_user_chat_message(
                sid,
                text,
                turn_id,
                req_id,
                origin="voice",
            )

        self._set_state(sid, THINKING, reason="asr_final")

    async def on_asr_timeout(
        self,
        sid: str,
        req_id: str | None,
        meta: dict | None = None,
    ) -> None:
        """
        Handle ASR timeouts with no recognized user text.
        This is a semantic event (no user_text); decide whether to re-engage the user.
        """

        meta = meta or {}
        session = self._ensure_session(sid)
        effective_req_id = req_id if isinstance(req_id, str) and req_id else session.req_id
        if not isinstance(effective_req_id, str) or not effective_req_id:
            effective_req_id = f"req-{uuid.uuid4().hex}"
        session.req_id = effective_req_id

        turn_id = session.turn_id or f"turn-{uuid.uuid4().hex}"
        session.turn_id = turn_id
        _log.info(
            "evt=voice.on_asr_timeout sid=%s req_id=%s meta=%s",
            sid,
            effective_req_id,
            meta,
        )

        system_signal = {
            "role": "system",
            "content": "[SYSTEM_EVENT: User was silent or ASR timed out. Briefly re-engage or ask if they are still there.]",
        }

        await self._apply_policy_decision_async(
            sid,
            {
                "req_id": effective_req_id,
                "turn_id": turn_id,
                "intent": "system.timeout_reengagement",
                "entities": {},
                "user_text": None,
                "timeout": True,
                "meta": meta,
                "injected_messages": [system_signal],
            },
        )

    def on_asr_open(self, sid: str, turn_id: str | None = None) -> None:
        """Transition into listening when the ASR stream opens for a turn."""

        session = self._ensure_session(sid)
        if session.state not in {READY, RESPONDING}:
            _log.debug(
                "evt=asr_v3.engine_asr_open_ignored sid=%s state=%s turn_id=%s",
                sid,
                session.state,
                turn_id,
            )
            return
        self._set_state(sid, LISTENING, reason="asr_open")
        session = self._ensure_session(sid)
        if session.state != LISTENING:
            return

        _log.info(
            "evt=asr_v3.engine_asr_open sid=%s state=%s turn_id=%s",
            sid,
            session.state,
            turn_id,
        )
        if session.turn_started_ms is None:
            session.turn_started_ms = _now_ms()
        if isinstance(turn_id, str) and turn_id:
            session.turn_id = turn_id

    def on_asr_partial(
        self,
        sid: str,
        req_id: str,
        confidence: float,
        partial_text: Optional[str] = None,
    ) -> None:
        session = self._ensure_session(sid)

        if session.state == READY:
            self._set_state(sid, LISTENING, reason="asr_partial")
            session = self._ensure_session(sid)
            if session.state != LISTENING:
                return

        if session.turn_started_ms is None:
            session.turn_started_ms = _now_ms()

        active_req_id = session.req_id
        if not isinstance(active_req_id, str) or not active_req_id:
            if isinstance(req_id, str) and req_id:
                active_req_id = req_id
            else:
                active_req_id = f"req-{uuid.uuid4().hex}"
            session.req_id = active_req_id
        elif isinstance(req_id, str) and req_id and req_id != active_req_id:
            _log.debug(
                "evt=asr_partial_req_id_mismatch sid=%s active_req_id=%s provided_req_id=%s",
                sid,
                active_req_id,
                req_id,
            )

        req_id = session.req_id or active_req_id

        if session.turn_id is None:
            session.turn_id = str(uuid.uuid4())

        if isinstance(partial_text, str):
            partial_value = partial_text
        elif partial_text:
            partial_value = str(partial_text)
        else:
            partial_value = ""

        payload: Dict[str, Any] = {
            "req_id": req_id,
            "text": partial_value,
            "confidence": float(confidence),
        }
        partial_event = self._envelope(sid, EVT_ASR_PARTIAL, payload)
        self._publish(partial_event)

        if (
            session.perf_first_partial_ms is None
            and session.turn_started_ms is not None
            and session.req_id == req_id
        ):
            elapsed = _now_ms() - session.turn_started_ms
            if elapsed < 0:
                elapsed = 0
            session.perf_first_partial_ms = elapsed

        aggregator = self._aggregators.get(sid)
        if aggregator is not None:
            aggregator.feed_asr_evidence(req_id, confidence, partial_text)
        self._commit_turn_start(sid, "asr_partial")

    def on_auto_barge_attempt(
        self, sid: str, source: str, *, reason: str | None = None
    ) -> None:
        """Handle an automatic barge-in attempt during assistant speech."""

        if source not in {"auto_vad", "asr_evidence"}:
            return

        session = self._ensure_session(sid)
        current_state = session.state

        policy = self.policy_snapshot or {}
        policy_enabled = bool(policy.get("barge_in_enabled"))

        granted = bool(policy_enabled and current_state == RESPONDING)

        deny_reason = reason
        if current_state != RESPONDING:
            granted = False
            deny_reason = deny_reason or "state_not_responding"
        elif not policy_enabled:
            granted = False
            deny_reason = deny_reason or "policy_disabled"

        detected_reason = reason if granted else deny_reason
        self._publish_barge_event(
            sid,
            source,
            event_type=EVT_BARGE_DETECTED,
            granted=granted,
            reason=detected_reason,
        )

        if granted:
            self._barge_attempts.pop(sid, None)
            self._barge_attempts[sid] = {
                "source": source,
                "deny_reason": deny_reason,
            }
            self.cancel_current_tts(sid, reason="canceled")
            self._set_state(sid, CONFIRMING_BARGE, reason="auto_barge")
            session = self._ensure_session(sid)
            if session.state == CONFIRMING_BARGE:
                self._schedule_barge_confirmation(sid)
            else:
                _log.warning(
                    "evt=auto_barge_ready_blocked state=%s",
                    session.state,
                    extra={"sid": sid, "event": "auto_barge_ready_blocked"},
                )
                self._reject_auto_barge(sid, "ready_state_blocked")
        else:
            reject_reason = deny_reason or "unknown"
            self._publish_barge_event(
                sid,
                source,
                event_type=EVT_BARGE_REJECTED,
                granted=False,
                reason=reject_reason,
            )
            _log.info(
                "evt=barge_auto_denied reason=%s source=%s",
                reject_reason,
                source,
                extra={"sid": sid, "event": "barge_auto_denied"},
            )

    def cancel_current_tts(self, sid: str, *, reason: str = "canceled") -> bool:
        """Cancel the active TTS stream for the session if present."""

        session = self._ensure_session(sid)
        utt_id = session.tts_utt_id
        if not utt_id:
            return False

        self._teardown_tts(
            sid,
            utt_id,
            reason=reason,
            post_hold_ms=0,
            transition_to_ready=False,
        )
        return True

    def reapply_policy(self, overrides: Dict[str, Any] | None = None) -> bool:
        """Reload and re-emit the interaction policy when it changes."""

        if not self._last_sid:
            return False

        sid = self._last_sid
        snapshot = load_interaction_policy(overrides)
        previous = self._policy_snapshot

        if not should_reapply(previous, snapshot):
            return False

        diff = compute_diff(previous, snapshot)
        self._policy_snapshot = dict(snapshot)

        apply_logging_policy(snapshot)
        self._emit_policy_frame(sid, snapshot)
        self._emit_policy_applied(sid, previous, diff)
        self._emit_acwr_breadcrumb(sid, snapshot)
        return True

    def _envelope(self, sid: str, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        envelope = _Envelope(event_type, sid, dict(payload))
        return envelope.to_dict()

    def _publish(self, event: Dict[str, Any]) -> None:
        self._bus.publish(dict(event))

    def _handle_outbound_chat_frame(self, event: Dict[str, Any]) -> None:
        if event.get("type") != EVT_WS_JSON_SEND:
            return
        sid = event.get("sid")
        if not isinstance(sid, str) or not sid:
            return
        frame: Mapping[str, Any] | None = None
        raw_frame = event.get("frame")
        if isinstance(raw_frame, Mapping):
            frame = raw_frame
        else:
            payload = event.get("payload")
            if isinstance(payload, Mapping):
                candidate = payload.get("frame")
                if isinstance(candidate, Mapping):
                    frame = candidate
        if frame is None:
            return
        if frame.get("type") != "chat.message":
            return
        self._conversation_buffer.append(sid, frame)

    def _emit_info_frame(self, sid: str) -> None:
        voice_id, locale = self._voice_profile()
        audio_descriptor = dict(_PCM_DESCRIPTOR)
        frame = {
            "type": "info",
            "audio": audio_descriptor,
            "slo": json.loads(json.dumps(_INFO_SLO)),
            "voice_id": voice_id,
            "locale": locale,
            "meta": {
                "sid": sid,
                "audio": audio_descriptor,
            },
        }
        serialized = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
        meta = {
            "ws": {
                "dir": "out",
                "size": len(serialized.encode("utf-8")),
                "preview": serialized,
            }
        }
        payload = {"meta": meta, "frame": frame}
        event = self._envelope(sid, EVT_WS_JSON_SEND, payload)
        self._publish(event)

    def _publish_gate_event(self, event: Dict[str, Any]) -> None:
        """Publish gate controller events through the standard telemetry path."""

        envelope = dict(event)
        sid = envelope.get("sid") or self._last_sid
        if not isinstance(sid, str) or not sid:
            return
        envelope.setdefault("sid", sid)
        envelope.setdefault("who", "server")
        envelope.setdefault("source", "voice_engine")
        self._publish(envelope)

    def _maybe_emit_greeting(self, sid: str) -> None:
        session = self._ensure_session(sid)
        if session.greet_emitted:
            return

        snapshot = self.policy_snapshot or {}
        greet_block = snapshot.get("greet")
        if isinstance(greet_block, Mapping):
            enabled = bool(greet_block.get("enabled", True))
            mode = greet_block.get("mode")
            post_hold = greet_block.get("post_hold_ms")
        else:
            enabled = True
            mode = "persona"
            post_hold = None

        if not enabled:
            return

        greet_mode = mode if isinstance(mode, str) and mode else "persona"
        post_hold_ms = None
        if isinstance(post_hold, (int, float)):
            candidate_post_hold = int(post_hold)
            if candidate_post_hold >= 0:
                post_hold_ms = candidate_post_hold

        turn_id = str(uuid.uuid4())
        req_id = f"req-{uuid.uuid4().hex}"
        plan_payload: Dict[str, Any] = {"mode": "greet", "reason": "session_open"}

        # Greet follows the same policy -> LLM -> NLG -> chat frame pipeline used by
        # subsequent user turns, anchored here to the session_open.greet rule.
        # TODO: Consider threading greet through a shared helper to avoid drift from
        # the main _maybe_emit_policy_and_nlg path when future changes land.

        persona: Dict[str, Any] = {}
        try:
            persona_candidate = load_persona()
        except Exception:
            _log.exception("evt=greet_persona_load_failed sid=%s", sid)
        else:
            if isinstance(persona_candidate, dict):
                persona = persona_candidate

        fallback_copy = persona.get("greet_copy") if isinstance(persona, dict) else None
        if not isinstance(fallback_copy, str) or not fallback_copy.strip():
            fallback_copy = "Hi there! I'm Chip. How can I help you today?"

        greeting_text: str
        if greet_mode == "persona":
            provider = getattr(self._llm, "_provider", None)
            model_name = getattr(provider, "default_model", None)
            if not isinstance(model_name, str) or not model_name.strip():
                model_name = "gpt-4o-mini"
            temperature = 0.4
            max_tokens = 30
            msg_counts = {"system": 1, "developer": 1, "user": 1}

            greet_actions = assistant_turn_actions(self.policy_snapshot)
            policy_payload = {"rule": "session_open.greet", "actions": greet_actions}
            policy_event = self._envelope(sid, EVT_POLICY_DECISION, policy_payload)
            self._publish(policy_event)

            _log.debug(
                "evt=greet_llm_request sid=%s purpose=%s model=%s temp=%s max_tokens=%s msg_counts=%s",
                sid,
                "greet",
                model_name,
                temperature,
                max_tokens,
                msg_counts,
            )

            request_payload = {
                "req_id": req_id,
                "purpose": "greet",
                "model": model_name,
                "temp": temperature,
                "max_tokens": max_tokens,
                "msg_counts": msg_counts,
            }
            request_event = self._envelope(
                sid, EVT_LLM_RESPONSE_START, request_payload
            )
            self._publish(request_event)

            greeting_candidate: str | None = None
            start_time = time.perf_counter()
            try:
                greeting_candidate = self._llm.generate_greeting(
                    sid,
                    turn_id,
                    req_id,
                    plan_payload,
                )
            except Exception:
                _log.exception("evt=greet_llm_failed sid=%s", sid)
                greeting_candidate = None
            else:
                latency_ms = max(int((time.perf_counter() - start_time) * 1000), 0)
                complete_payload = {
                    "req_id": req_id,
                    "purpose": "greet",
                    "latency_ms": latency_ms,
                    "model": model_name,
                }
                complete_event = self._envelope(
                    sid, EVT_LLM_RESPONSE_END, complete_payload
                )
                self._publish(complete_event)
                _log.debug(
                    "evt=greet_llm_complete sid=%s purpose=%s latency_ms=%s model=%s",
                    sid,
                    "greet",
                    latency_ms,
                    model_name,
                )
            greeting_text = greeting_candidate if isinstance(greeting_candidate, str) else ""
        else:
            greeting_text = ""

        if not greeting_text.strip():
            greeting_text = fallback_copy.strip()

        nlg_payload: Dict[str, Any] = {
            "req_id": req_id,
            "text": greeting_text,
            "meta": {"reason": "greet"},
        }
        if post_hold_ms is not None:
            nlg_payload.setdefault("meta", {})["post_hold_ms"] = post_hold_ms
        nlg_event = self._envelope(sid, EVT_NLG, nlg_payload)
        self._publish(nlg_event)

        frame = {
            "type": "chat.message",
            "id": str(uuid.uuid4()),
            "role": "assistant",
            "text": greeting_text,
            "origin": "voice",
            "turn_id": turn_id,
            "req_id": req_id,
            "ts_ms": _now_ms(),
        }
        serialized = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
        payload = {
            "meta": {
                "ws": {
                    "dir": "out",
                    "size": len(serialized.encode("utf-8")),
                    "preview": serialized,
                }
            },
            "frame": frame,
        }
        event = self._envelope(sid, EVT_WS_JSON_SEND, payload)
        self._publish(event)

        session.greet_emitted = True

    def _maybe_emit_connect_suggestions(self, sid: str) -> None:
        session = self._ensure_session(sid)
        if session.suggestions_emitted:
            return

        snapshot = self.policy_snapshot or {}
        suggestions_block = snapshot.get("suggestions")
        if isinstance(suggestions_block, Mapping):
            on_connect = bool(suggestions_block.get("on_connect"))
            count_candidate = suggestions_block.get("count")
        else:
            on_connect = False
            count_candidate = None

        actions_block = snapshot.get("actions")
        surface_actions = True
        if isinstance(actions_block, Mapping):
            surface_candidate = actions_block.get("surface_via_suggestions")
            if isinstance(surface_candidate, bool):
                surface_actions = surface_candidate

        if not on_connect:
            return

        if not surface_actions:
            return

        max_items = 3
        if isinstance(count_candidate, (int, float)):
            normalized = int(count_candidate)
            if normalized > 0:
                max_items = normalized

        try:
            persona = load_persona()
            persona_dict = persona if isinstance(persona, Mapping) else {}
        except Exception:
            _log.exception("evt=connect_persona_load_failed sid=%s", sid)
            persona_dict = {}

        def _normalize_labels(labels: Any) -> list[str]:
            seen = set()
            results = []
            if isinstance(labels, list):
                for label in labels:
                    if not isinstance(label, str):
                        continue
                    stripped = label.strip()
                    if not stripped or stripped in seen:
                        continue
                    seen.add(stripped)
                    results.append(stripped)
            return results

        selected_mode: Optional[str] = None
        normalized_items: list[str] = []

        for mode_key in ("greet", "outline"):
            normalized_items = _normalize_labels(
                default_chips_for_mode(persona_dict, mode_key)
            )
            if normalized_items:
                selected_mode = mode_key
                break

        if not normalized_items:
            normalized_items = _normalize_labels(_MODE_CHIPS.get("clarify", []))
            if normalized_items:
                selected_mode = "clarify"

        if not normalized_items or not selected_mode:
            return

        limited_items = normalized_items[:max_items]
        if not limited_items:
            return

        suggestion_items = []
        for label in limited_items:
            item: Dict[str, Any] = {"label": label, "kind": "action"}
            item["id"] = _slugify_action_label(label)
            suggestion_items.append(item)

        suggestions_frame = {
            "type": "assistant.suggestions",
            "ts_ms": _now_ms(),
            "items": suggestion_items,
            "mode": selected_mode,
        }
        serialized = json.dumps(suggestions_frame, ensure_ascii=False, separators=(",", ":"))
        payload = {
            "meta": {
                "ws": {
                    "dir": "out",
                    "size": len(serialized.encode("utf-8")),
                    "preview": serialized,
                }
            },
            "frame": suggestions_frame,
        }
        event = self._envelope(sid, EVT_WS_JSON_SEND, payload)
        self._publish(event)

        session.suggestions_emitted = True

    def _publish_barge_event(
        self,
        sid: str,
        source: str,
        *,
        event_type: str,
        granted: bool,
        reason: str | None = None,
    ) -> None:
        """Publish lifecycle-specific barge telemetry events."""

        if source not in {"auto_vad", "asr_evidence", "text"}:
            return

        meta: Dict[str, Any] = {
            "barge": {
                "source": source,
                "granted": bool(granted),
            }
        }
        if reason is not None:
            meta["barge"]["reason"] = str(reason)
        event = self._envelope(sid, event_type, {"meta": meta})
        self._publish(event)

    def _schedule_barge_confirmation(self, sid: str) -> None:
        """Transition to listening after a short delay for mask clearing."""

        async def _confirm() -> None:
            await asyncio.sleep(0.42)
            self._complete_auto_barge(sid)

        self._cancel_barge_confirmation(sid)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None:
            task = loop.create_task(_confirm())
            self._barge_handles[sid] = task
        else:
            timer = threading.Timer(0.42, self._complete_auto_barge, args=(sid,))
            timer.daemon = True
            timer.start()
            self._barge_handles[sid] = timer

    def _cancel_barge_confirmation(self, sid: str, *, reject_reason: str | None = None) -> None:
        handle = self._barge_handles.pop(sid, None)
        if handle is None:
            handle_cancelled = False
        else:
            handle_cancelled = True
            if isinstance(handle, asyncio.Task) and not handle.done():
                handle.cancel()
            elif isinstance(handle, threading.Timer):
                handle.cancel()

        if reject_reason is not None:
            if handle_cancelled or sid in self._barge_attempts:
                self._reject_auto_barge(sid, reject_reason)
        elif handle is None:
            return

    def _complete_auto_barge(self, sid: str) -> None:
        self._barge_handles.pop(sid, None)
        attempt = self._barge_attempts.get(sid)
        if attempt is None:
            return

        session = self._ensure_session(sid)
        if session.state not in {READY, CONFIRMING_BARGE}:
            self._reject_auto_barge(sid, "confirmation_state_changed")
            return

        self._barge_attempts.pop(sid, None)
        source = str(attempt.get("source", "auto_vad"))
        self._publish_barge_event(
            sid,
            source,
            event_type=EVT_BARGE_CONFIRMED,
            granted=True,
        )
        self._set_state(sid, LISTENING, reason="auto_barge_confirmed")
        self._gate.set_reason("tts_active", False, sid=sid)
        self._commit_turn_start(sid, "server_vad")

    def _reject_auto_barge(self, sid: str, reason: str) -> None:
        attempt = self._barge_attempts.pop(sid, None)
        if attempt is None:
            return
        source = str(attempt.get("source", "auto_vad"))
        reject_reason = reason or str(attempt.get("deny_reason") or "unknown")
        self._publish_barge_event(
            sid,
            source,
            event_type=EVT_BARGE_REJECTED,
            granted=False,
            reason=reject_reason,
        )

    async def _release_system_hold_after(self, sid: str, post_hold_ms: int) -> None:
        """Release the system_hold reason after the requested delay."""

        await asyncio.sleep(post_hold_ms / 1000)
        self._gate.set_reason("system_hold", False, sid=sid)
        self._set_state(sid, READY, reason="tts_end")

    def _ensure_session(self, sid: str) -> _TurnSession:
        session = self._sessions.get(sid)
        if session is None:
            session = _TurnSession()
            self._sessions[sid] = session
            self._install_vad_aggregator(sid)
        return session

    def _install_vad_aggregator(self, sid: str) -> None:
        if sid in self._aggregators:
            return

        def _policy_supplier() -> Dict[str, Any]:
            snapshot = self.policy_snapshot or {}
            return dict(snapshot)

        aggregator = VADAggregator(self._bus, sid, _policy_supplier)
        aggregator.set_grant_handler(
            lambda source, info, *, _sid=sid: self._handle_vad_grant(_sid, source, info)
        )
        self._aggregators[sid] = aggregator

    def enable_full_duplex(self, sid: str) -> None:
        """Allow VAD to run during TTS playback after greet completion."""

        aggregator = self._aggregators.get(sid)
        if aggregator is not None:
            aggregator.enable_full_duplex()

    def _handle_vad_grant(self, sid: str, source: str, info: Dict[str, Any]) -> None:
        reason = "vad_grant"
        mode = info.get("mode") if isinstance(info, Mapping) else None
        if mode == "and":
            reason = "vad_grant_dual"
        self.on_auto_barge_attempt(sid, source, reason=reason)

    def _set_state(self, sid: str, new_state: str, *, reason: str | None = None) -> None:
        """Transition the session state machine and emit telemetry breadcrumbs."""

        session = self._ensure_session(sid)
        previous = session.state
        if previous == new_state:
            _log.warning(
                "evt=state_transition_duplicate state=%s", new_state, extra={"sid": sid}
            )
            return

        allowed = _ALLOWED_TRANSITIONS.get(previous, set())
        if new_state not in allowed:
            prev_rank = _STATE_ORDER.get(previous, -1)
            next_rank = _STATE_ORDER.get(new_state, -1)
            relation = "unknown"
            if prev_rank >= 0 and next_rank >= 0:
                relation = "forward" if next_rank > prev_rank else "backward"
            _log.warning(
                "evt=state_transition_illegal from=%s to=%s relation=%s",
                previous,
                new_state,
                relation,
                extra={"sid": sid},
            )
            return

        if new_state == LISTENING:
            self._publish_tts_mask(sid, "off")
            if session.tts_mask_phase != "off":
                _log.warning(
                    "evt=state_transition_mask_blocked", extra={"sid": sid}
                )
                return

        now_ms = _now_ms()

        if new_state == LISTENING:
            session.turn_id = str(uuid.uuid4())
            session.req_id = f"req-{uuid.uuid4().hex}"
            session.nlu_req_id = None
            session.turn_started_ms = now_ms
            session.perf_first_partial_ms = None
            session.perf_final_ms = None
            session.perf_tts_start_ms = None
            session.asr_final_emitted = False
            session.nlu_emitted = False
            session.policy_emitted = False
            session.nlg_emitted = False
            session.plan_emitted = False
            session.suggestions_emitted = False
            session.plan = None
            session.tts_mask_phase = "off"
            session.turn_committed = False
            session._vad_energy_logged = False
            session.assistant_turn_open = False
            session.history_message_count = 0
            session.answer_chars = None
            session.metrics_asr_final_ms = None
            session.metrics_llm_start_ms = None
            session.metrics_llm_end_ms = None
            session.metrics_tts_start_ms = None
            session.metrics_tts_first_chunk_ms = None
            session.metrics_tts_end_ms = None
            session.metrics_logged_stages.clear()

        if (
            new_state == READY
            and previous in {LISTENING, THINKING, RESPONDING}
            and session.turn_id
        ):
            start_ms = session.turn_started_ms or now_ms
            duration_ms = now_ms - start_ms
            if duration_ms <= 0:
                duration_ms = 1
            turn_id = session.turn_id
            req_id = session.req_id
            end_payload = {
                "turn_id": turn_id,
                "req_id": req_id,
                "meta": {
                    "turn_id": turn_id,
                    "req_id": req_id,
                    "duration_ms": duration_ms,
                },
            }
            end_event = self._envelope(sid, EVT_TURN_END, end_payload)
            self._publish(end_event)
            summary_payload = {
                "turn_id": turn_id,
                "req_id": req_id,
                "t_first_partial_ms": session.perf_first_partial_ms,
                "t_final_ms": session.perf_final_ms,
                "t_tts_start_ms": session.perf_tts_start_ms,
            }
            perf_event = self._envelope(sid, EVT_PERF_SUMMARY, summary_payload)
            self._publish(perf_event)
            session.turn_id = None
            session.turn_started_ms = None
            session.tts_utt_id = None
            session.req_id = None
            session.nlu_req_id = None
            session.perf_first_partial_ms = None
            session.perf_final_ms = None
            session.perf_tts_start_ms = None
            session.asr_final_emitted = False
            session.nlu_emitted = False
            session.policy_emitted = False
            session.nlg_emitted = False
            session.plan_emitted = False
            session.suggestions_emitted = False
            session.plan = None
            session.turn_committed = False
            session._vad_energy_logged = False
            session.assistant_turn_open = False
            session.history_message_count = 0
            session.answer_chars = None
            session.metrics_asr_final_ms = None
            session.metrics_llm_start_ms = None
            session.metrics_llm_end_ms = None
            session.metrics_tts_start_ms = None
            session.metrics_tts_first_chunk_ms = None
            session.metrics_tts_end_ms = None
            session.metrics_logged_stages.clear()

        session.state = new_state

        aggregator = self._aggregators.get(sid)
        if aggregator is not None:
            aggregator.on_engine_mode_change(new_state)

        breadcrumb_meta: Dict[str, Any] = {"state": new_state}
        if reason is not None:
            breadcrumb_meta["reason"] = reason
        breadcrumb_payload = {"meta": breadcrumb_meta}
        breadcrumb_event = self._envelope(sid, EVT_TURN_STATE, breadcrumb_payload)
        self._publish(breadcrumb_event)

    def _commit_turn_start(self, sid: str, source: str) -> None:
        """Emit EVT_TURN_BEGIN once authoritative speech evidence arrives."""

        session = self._sessions.get(sid)
        if session is None or session.state != LISTENING:
            return
        if session.turn_committed:
            return

        turn_id = session.turn_id
        req_id = session.req_id
        if not isinstance(turn_id, str) or not turn_id:
            return
        if not isinstance(req_id, str) or not req_id:
            return

        session.turn_committed = True
        if session.turn_started_ms is None:
            session.turn_started_ms = _now_ms()

        meta: Dict[str, Any] = {
            "turn_id": turn_id,
            "req_id": req_id,
            "state": LISTENING,
            "commit_source": source,
        }
        begin_payload = {"turn_id": turn_id, "req_id": req_id, "meta": meta}
        begin_event = self._envelope(sid, EVT_TURN_BEGIN, begin_payload)
        self._publish(begin_event)

    def turn_context(self, sid: str) -> Optional[Dict[str, str]]:
        """Return the active turn context for ``sid`` if a turn is in progress."""

        session = self._sessions.get(sid)
        if (
            session is None
            or session.turn_id is None
            or session.req_id is None
            or session.state == READY
            or not session.turn_committed
        ):
            return None

        return {"turn_id": session.turn_id, "req_id": session.req_id}

    def _voice_profile(self) -> tuple[str, str]:
        """Return the active voice identifier and locale.

        Preference order:

        1. Current policy snapshot voice block (``policy["voice"]``).
        2. Top-level ``voice_id``/``locale`` keys in the snapshot.
        3. Static server defaults when the policy omits the fields.
        """

        snapshot = self.policy_snapshot or {}
        voice_id: Optional[str] = None
        locale: Optional[str] = None

        voice_block = snapshot.get("voice")
        if isinstance(voice_block, Mapping):
            voice_candidate = voice_block.get("voice_id")
            if isinstance(voice_candidate, str) and voice_candidate:
                voice_id = voice_candidate
            locale_candidate = voice_block.get("locale")
            if isinstance(locale_candidate, str) and locale_candidate:
                locale = locale_candidate

        top_level_voice = snapshot.get("voice_id")
        if isinstance(top_level_voice, str) and top_level_voice:
            voice_id = voice_id or top_level_voice

        top_level_locale = snapshot.get("locale")
        if isinstance(top_level_locale, str) and top_level_locale:
            locale = locale or top_level_locale

        if not voice_id:
            voice_id = _DEFAULT_VOICE_ID
        if not locale:
            locale = _DEFAULT_LOCALE

        return voice_id, locale

    def _publish_tts_mask(self, sid: str, phase: str, *, force: bool = False) -> None:
        session = self._ensure_session(sid)
        phase_value = str(phase)
        current = session.tts_mask_phase
        if not force and current == phase_value:
            return
        session.tts_mask_phase = phase_value
        mask_event = self._envelope(sid, EVT_TTS_MASK, {"phase": phase_value})
        self._publish(mask_event)

    def _publish_action_say_end(
        self,
        sid: str,
        utt_id: str,
        *,
        reason: str,
        req_id: str | None,
    ) -> None:
        payload: Dict[str, Any] = {"meta": {"tts": {"utt_id": utt_id}}, "reason": reason}
        if isinstance(req_id, str) and req_id:
            payload["req_id"] = req_id
        event = self._envelope(sid, EVT_ACTION_SAY_END, payload)
        self._publish(event)

    def _teardown_tts(
        self,
        sid: str,
        utt_id: str,
        *,
        reason: str | None = None,
        post_hold_ms: int = 0,
        transition_to_ready: bool,
        is_greet: bool = False,
    ) -> None:
        self._streaming.set_output_finalizer(sid, None)
        session = self._ensure_session(sid)
        self._record_turn_timing(sid, session, "tts_end")
        was_active = session.tts_utt_id == utt_id
        if was_active:
            session.tts_utt_id = None
        session.assistant_turn_open = False

        aggregator = self._aggregators.get(sid)
        if aggregator is not None:
            aggregator.on_tts_end()

        tts_meta: Dict[str, Any] = {"utt_id": utt_id}
        if is_greet:
            tts_meta["is_greet"] = True
        payload: Dict[str, Any] = {"meta": {"tts": tts_meta}}
        if is_greet:
            payload["meta"]["is_greet"] = True
        req_id = session.req_id
        req_id_value = req_id if isinstance(req_id, str) and req_id else None
        if req_id_value:
            payload["req_id"] = req_id_value
        if reason:
            payload["reason"] = reason

        _log.info(
            "TURN_METRICS stage=tts_end sid=%s utt_id=%s reason=%s is_greet=%s",
            sid,
            utt_id,
            reason,
            is_greet,
        )

        event = self._envelope(sid, EVT_TTS_END, payload)
        self._publish(event)

        if reason:
            self._publish_action_say_end(
                sid,
                utt_id,
                reason=reason,
                req_id=req_id_value,
            )

        self._publish_tts_mask(sid, "off", force=was_active)
        self._gate.set_reason(
            "tts_active", False, sid=sid, meta={"tts": {"utt_id": utt_id}}
        )

        if transition_to_ready:
            if post_hold_ms > 0:
                self._gate.set_reason(
                    "system_hold",
                    True,
                    sid=sid,
                    meta={"tts": {"utt_id": utt_id, "post_hold_ms": post_hold_ms}},
                )
                asyncio.create_task(self._release_system_hold_after(sid, post_hold_ms))
            else:
                self._set_state(sid, READY, reason="tts_end")

    def _snapshot_with_adaptive_overrides(
        self, snapshot: Mapping[str, Any], session: _TurnSession
    ) -> Dict[str, Any]:
        result = dict(snapshot)
        ue_override = getattr(session, "adaptive_utterance_end_ms", None)
        cs_override = getattr(session, "adaptive_commit_silence_ms", None)
        if not any(
            isinstance(value, (int, float)) and value > 0
            for value in (ue_override, cs_override)
        ):
            return result

        policy_block = snapshot.get("policy")
        if isinstance(policy_block, Mapping):
            policy_copy: Dict[str, Any] = dict(policy_block)
        else:
            policy_copy = {}
        asr_block = policy_copy.get("asr") if isinstance(policy_copy, Mapping) else None
        if isinstance(asr_block, Mapping):
            asr_copy: Dict[str, Any] = dict(asr_block)
        else:
            asr_copy = {}
        if isinstance(ue_override, (int, float)) and ue_override > 0:
            asr_copy["utterance_end_ms"] = int(ue_override)
        if isinstance(cs_override, (int, float)) and cs_override > 0:
            asr_copy["commit_silence_ms"] = int(cs_override)
        policy_copy["asr"] = asr_copy
        result["policy"] = policy_copy
        return result

    def _emit_policy_frame(self, sid: str, snapshot: Dict[str, Any]) -> None:
        session = self._ensure_session(sid)

        if isinstance(snapshot, Mapping):
            snapshot_for_frame = self._snapshot_with_adaptive_overrides(snapshot, session)
        else:
            snapshot_for_frame = dict(snapshot)

        nested_sequence: list[Any] | None = None
        actions_block = snapshot_for_frame.get("actions")
        if isinstance(actions_block, Mapping):
            candidate = actions_block.get("assistant_turn_sequence")
            if isinstance(candidate, Sequence) and not isinstance(candidate, (str, bytes)):
                nested_sequence = list(candidate)

        derived_actions = assistant_turn_actions(snapshot_for_frame)
        summary_actions = (
            list(nested_sequence)
            if nested_sequence is not None
            else list(derived_actions)
        )

        frame = {
            "type": "policy.interaction",
            "policy": snapshot_for_frame,
            "actions": summary_actions,
        }

        mismatch = nested_sequence is None or summary_actions != nested_sequence
        if mismatch and not session.policy_actions_mismatch_logged:
            _log.warning(
                "evt=policy_actions_mismatch nested=%s summary=%s derived=%s",
                nested_sequence,
                summary_actions,
                derived_actions,
                extra={"sid": sid},
            )
            session.policy_actions_mismatch_logged = True

        preview = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
        meta = {"ws": {"dir": "out", "size": len(preview.encode("utf-8")), "preview": preview}}
        payload = {"meta": meta, "frame": frame}
        event = self._envelope(sid, EVT_WS_JSON_SEND, payload)
        self._publish(event)

    def _emit_dialog_plan(
        self,
        sid: str,
        session: _TurnSession,
        req_id: str,
        turn_id: str,
        user_text: str,
    ) -> None:
        if session.plan_emitted:
            return

        if not isinstance(req_id, str) or not req_id:
            return
        if not isinstance(turn_id, str) or not turn_id:
            return

        plan = plan_turn(user_text)
        plan_payload = {
            "req_id": req_id,
            "turn_id": turn_id,
            "plan": {
                "mode": plan.mode,
                "missing_info": list(plan.missing_info),
                "chips": list(plan.chips),
                "reason": plan.reason,
            },
        }
        plan_event = self._envelope(sid, EVT_DIALOG_PLAN, plan_payload)
        self._publish(plan_event)

        frame = {
            "type": "dialog.plan",
            "ts_ms": _now_ms(),
            "mode": plan.mode,
            "missing_info": list(plan.missing_info),
            "chips": list(plan.chips),
            "reason": plan.reason,
        }
        serialized = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
        meta = {
            "ws": {
                "dir": "out",
                "size": len(serialized.encode("utf-8")),
                "preview": serialized,
            }
        }
        payload = {"meta": meta, "frame": frame}
        frame_event = self._envelope(sid, EVT_WS_JSON_SEND, payload)
        self._publish(frame_event)

        if not session.suggestions_emitted:
            chips: list[str] = []
            for chip in list(plan.chips):
                if isinstance(chip, str):
                    label = chip.strip()
                    if label:
                        chips.append(label)
                if len(chips) >= 3:
                    break

            if not chips:
                try:
                    persona_candidate = load_persona()
                except Exception:
                    persona_candidate = None
                persona = persona_candidate if isinstance(persona_candidate, dict) else {}
                fallback = default_chips_for_mode(persona, plan.mode)
                chips = [
                    str(item).strip()
                    for item in fallback
                    if isinstance(item, str) and str(item).strip()
                ][:3]

            if chips:
                suggestions_frame = {
                    "type": "assistant.suggestions",
                    "ts_ms": _now_ms(),
                    "items": [{"label": label, "kind": "action"} for label in chips],
                    "mode": plan.mode,
                }
                suggestions_serialized = json.dumps(
                    suggestions_frame, ensure_ascii=False, separators=(",", ":")
                )
                suggestions_meta = {
                    "ws": {
                        "dir": "out",
                        "size": len(suggestions_serialized.encode("utf-8")),
                        "preview": suggestions_serialized,
                    }
                }
                suggestions_payload = {"meta": suggestions_meta, "frame": suggestions_frame}
                suggestions_event = self._envelope(sid, EVT_WS_JSON_SEND, suggestions_payload)
                self._publish(suggestions_event)
                session.suggestions_emitted = True

        session.plan_emitted = True
        session.plan = {
            "req_id": req_id,
            "turn_id": turn_id,
            "mode": plan.mode,
            "missing_info": list(plan.missing_info),
            "chips": list(plan.chips),
            "reason": plan.reason,
            "user_text": (user_text or "").strip(),
        }

    def _apply_policy_decision(
        self, sid: str, nlu_payload: Mapping[str, Any]
    ) -> None:
        """Bridge NLU results into the policy/LLM pipeline."""

        session = self._ensure_session(sid)
        self._maybe_emit_policy_and_nlg(sid, session, nlu_payload)

    async def _apply_policy_decision_async(
        self, sid: str, nlu_payload: Mapping[str, Any]
    ) -> None:
        self._apply_policy_decision(sid, nlu_payload)

    def _maybe_emit_policy_and_nlg(
        self,
        sid: str,
        session: _TurnSession,
        nlu_payload: Mapping[str, Any],
    ) -> None:
        """Run the post-ASR policy decision and trigger the LLM/NLG pipeline.

        Inputs:
            * sid: Session identifier for telemetry correlation.
            * session: The mutable per-session _TurnSession state.
            * nlu_payload: NLU output for the current user turn (req_id, turn_id, intent, entities).

        decision["action"] governs downstream behavior:
            * "respond" → emit policy telemetry, invoke the LLM once, emit EVT_LLM_RESPONSE_START/END, then emit EVT_NLG and chat frames.
            * "ignore"/"await_user"/other → log the policy decision but skip LLM/NLG emission.

        Expected decisions:
            * Greet: anchored to session_open.greet and emits a respond-like action via the greet path.
            * Normal user chat turns: default to action="respond" so real user finals yield LLM + NLG.
            * Diagnostic or system-hold scenarios: policy may return non-respond actions to explicitly suppress LLM.
        """
        if session.policy_emitted:
            return

        req_id = session.req_id or nlu_payload.get("req_id")
        if not isinstance(req_id, str) or not req_id:
            return

        snapshot = self.policy_snapshot or {}
        decision = self._policy_decider.decide(req_id, nlu_payload, snapshot)
        policy_payload = {"req_id": req_id, **decision}
        policy_event = self._envelope(sid, EVT_POLICY_DECISION, policy_payload)
        self._publish(policy_event)
        session.policy_emitted = True

        gate_snapshot = self._gate.snapshot()
        gate_reasons = gate_snapshot.get("reasons") if isinstance(gate_snapshot, Mapping) else {}
        system_hold = bool(gate_reasons.get("system_hold")) if isinstance(gate_reasons, Mapping) else False
        diag_mode = bool(snapshot.get("diag_mode")) if isinstance(snapshot, Mapping) else False

        _log.info(
            "evt=voice.policy_decision sid=%s req_id=%s action=%s intent=%s diag_mode=%s system_hold=%s",
            sid,
            req_id,
            decision.get("action"),
            nlu_payload.get("intent"),
            diag_mode,
            system_hold,
        )

        # Policy drives the LLM (EVT_LLM_RESPONSE_START/END) and the synthesized
        # NLG/chat frames that ultimately reach the client for each ASR final.

        action = decision.get("action") or "respond"

        if action != "respond" or session.nlg_emitted:
            _log.info(
                "evt=voice.llm_turn_skipped sid=%s req_id=%s action=%s diag_mode=%s system_hold=%s nlg_emitted=%s",
                sid,
                req_id,
                action,
                diag_mode,
                system_hold,
                session.nlg_emitted,
            )
            return

        intent = nlu_payload.get("intent")
        if not isinstance(intent, str) or not intent:
            intent = "chitchat.fallback"

        entities = nlu_payload.get("entities")
        if isinstance(entities, Mapping):
            entity_payload = dict(entities)
        else:
            entity_payload = {}

        injected_messages: Sequence[Mapping[str, Any]] | None = None
        if intent == "system.timeout_reengagement":
            candidate_injected = nlu_payload.get("injected_messages")
            if isinstance(candidate_injected, Sequence) and not isinstance(
                candidate_injected, (str, bytes)
            ):
                injected_messages = list(candidate_injected)

        plan_context = session.plan if isinstance(session.plan, Mapping) else None
        plan_mode = None
        if plan_context is not None:
            plan_req_id = plan_context.get("req_id")
            if plan_req_id == req_id:
                candidate_mode = plan_context.get("mode")
                if isinstance(candidate_mode, str) and candidate_mode:
                    plan_mode = candidate_mode

        extra_style_instruction = (
            "Respond as if speaking aloud in a live call. "
            "Use ONE short spoken sentence, no more than about 12 words, "
            "unless the user explicitly asks for more detail."
        )

        self._prepare_llm_history(
            sid,
            session,
            req_id,
            extra_system_instruction=extra_style_instruction,
            injected_messages=injected_messages,
        )

        provider = getattr(self._llm, "_provider", None)
        model_name = getattr(provider, "default_model", None)
        request_payload: Dict[str, Any] = {"req_id": req_id, "purpose": "answer"}
        if intent:
            request_payload["intent"] = intent
        if plan_mode:
            request_payload["mode"] = plan_mode
        if entity_payload:
            request_payload["entity_count"] = len(entity_payload)
        request_payload["style"] = "one short spoken sentence, no more than 12 words"
        request_payload["style_instruction"] = extra_style_instruction
        request_payload["max_tokens"] = _CONCISE_MAX_TOKENS
        if session.history_message_count:
            request_payload["history_messages"] = session.history_message_count

        _log.info(
            "evt=voice.llm_turn_start sid=%s req_id=%s intent=%s diag_mode=%s system_hold=%s",
            sid,
            req_id,
            intent,
            diag_mode,
            system_hold,
        )

        self._record_turn_timing(sid, session, "llm_start")
        self._emit_assistant_turn_begin(sid, session)

        request_event = self._envelope(
            sid, EVT_LLM_RESPONSE_START, request_payload
        )
        self._publish(request_event)

        llm_start = time.perf_counter()
        response_text: str

        if plan_mode:
            turn_id = session.turn_id or plan_context.get("turn_id") or nlu_payload.get("turn_id")
            if not isinstance(turn_id, str) or not turn_id:
                turn_id = f"turn-{uuid.uuid4().hex}"
            user_text = plan_context.get("user_text") if isinstance(plan_context, Mapping) else ""
            if not isinstance(user_text, str):
                user_text = ""
            plan_payload: Dict[str, Any] = {
                "mode": plan_mode,
                "missing_info": list(plan_context.get("missing_info") or []),
                "chips": list(plan_context.get("chips") or []),
                "reason": plan_context.get("reason"),
            }
            response_text = self._llm.generate_persona(
                sid,
                turn_id,
                req_id,
                user_text,
                plan_payload,
                max_tokens=_CONCISE_MAX_TOKENS,
            )
        else:
            llm_result = self._llm.generate(
                req_id,
                intent=intent,
                entities=entity_payload,
                extra_system_instructions=extra_style_instruction,
                max_tokens=_CONCISE_MAX_TOKENS,
            )
            if isinstance(llm_result, Mapping):
                response_text = llm_result.get("text")
            else:
                response_text = llm_result

        answer_len = len(response_text or "") if isinstance(response_text, str) else 0
        session.answer_chars = answer_len

        self._record_turn_timing(sid, session, "llm_end")

        latency_ms = max(int((time.perf_counter() - llm_start) * 1000), 0)
        complete_payload: Dict[str, Any] = {
            "req_id": req_id,
            "purpose": "answer",
            "latency_ms": latency_ms,
            "answer_chars": answer_len,
        }
        if isinstance(model_name, str) and model_name:
            complete_payload["model"] = model_name
        complete_event = self._envelope(
            sid, EVT_LLM_RESPONSE_END, complete_payload
        )
        self._publish(complete_event)

        if not isinstance(response_text, str):
            response_text = str(response_text)

        nlg_payload = {"req_id": req_id, "text": response_text}
        nlg_event = self._envelope(sid, EVT_NLG, nlg_payload)
        self._publish(nlg_event)
        session.nlg_emitted = True

        self._emit_assistant_streaming_chat(sid, session, req_id, response_text)
        self._llm.publish_chat_message(req_id, response_text)

    def _emit_user_chat_message(
        self,
        sid: str,
        text: str,
        turn_id: str,
        req_id: str,
        client_msg_id: Optional[str] = None,
        *,
        origin: str = "text",
    ) -> None:
        frame: Dict[str, Any] = {
            "type": "chat.message",
            "id": str(uuid.uuid4()),
            "role": "user",
            "text": text,
            "origin": origin,
            "turn_id": turn_id,
            "req_id": req_id,
            "ts_ms": _now_ms(),
        }
        if client_msg_id is not None:
            frame["client_msg_id"] = client_msg_id

        serialized = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
        meta = {
            "ws": {
                "dir": "out",
                "size": len(serialized.encode("utf-8")),
                "preview": serialized,
            }
        }
        payload = {"meta": meta, "frame": frame}
        event = self._envelope(sid, EVT_WS_JSON_SEND, payload)
        self._publish(event)

    def _publish_chat_frame(self, sid: str, frame: Dict[str, Any]) -> None:
        serialized = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
        payload = {
            "meta": {
                "ws": {
                    "dir": "out",
                    "size": len(serialized.encode("utf-8")),
                    "preview": serialized,
                }
            },
            "frame": frame,
        }
        event = self._envelope(sid, EVT_WS_JSON_SEND, payload)
        self._publish(event)

    def _emit_assistant_streaming_chat(
        self, sid: str, session: _TurnSession, req_id: str, text: str
    ) -> None:
        if not isinstance(req_id, str) or not req_id:
            return

        turn_id = session.turn_id
        if not isinstance(turn_id, str) or not turn_id:
            turn_id = f"turn-{uuid.uuid4().hex}"
            session.turn_id = turn_id

        begin_frame = {
            "type": "chat.begin",
            "id": turn_id,
            "role": "assistant",
            "turn_id": turn_id,
            "req_id": req_id,
            "ts_ms": _now_ms(),
        }
        self._publish_chat_frame(sid, begin_frame)

        total_len = 0
        for chunk in _iter_sentence_chunks(text):
            total_len += len(chunk)
            delta_frame = {
                "type": "chat.delta",
                "id": turn_id,
                "append": chunk,
                "total_len": total_len,
                "turn_id": turn_id,
                "req_id": req_id,
                "ts_ms": _now_ms(),
            }
            self._publish_chat_frame(sid, delta_frame)

        commit_frame = {
            "type": "chat.commit",
            "id": turn_id,
            "total_len": total_len,
            "turn_id": turn_id,
            "req_id": req_id,
            "ts_ms": _now_ms(),
        }
        self._publish_chat_frame(sid, commit_frame)

        end_frame = {
            "type": "chat.end",
            "id": turn_id,
            "turn_id": turn_id,
            "req_id": req_id,
            "ts_ms": _now_ms(),
        }
        self._publish_chat_frame(sid, end_frame)

    def emit_static_assistant_response(
        self, sid: str, text: str, *, req_id: str | None = None, reason: str | None = None
    ) -> None:
        if not isinstance(text, str):
            return
        normalized_text = text.strip()
        if not normalized_text:
            return

        session = self._ensure_session(sid)
        if session.state != RESPONDING:
            self._set_state(sid, RESPONDING, reason="static_response")

        effective_req_id = req_id if isinstance(req_id, str) and req_id else session.req_id
        if not isinstance(effective_req_id, str) or not effective_req_id:
            effective_req_id = f"req-{uuid.uuid4().hex}"
        session.req_id = effective_req_id

        _log.info(
            "evt=static_nudge_emitted sid=%s req_id=%s reason=%s",
            sid,
            effective_req_id,
            reason,
        )

        turn_id = session.turn_id or f"turn-{uuid.uuid4().hex}"
        session.turn_id = turn_id
        session.answer_chars = len(normalized_text)
        session.nlg_emitted = True

        self._emit_assistant_turn_begin(sid, session)

        nlg_payload: Dict[str, Any] = {"req_id": effective_req_id, "text": normalized_text}
        if isinstance(reason, str) and reason:
            nlg_payload["meta"] = {"reason": reason}
        nlg_event = self._envelope(sid, EVT_NLG, nlg_payload)
        self._publish(nlg_event)

        self._emit_assistant_streaming_chat(sid, session, effective_req_id, normalized_text)

        publish_chat = getattr(self._llm, "publish_chat_message", None)
        if callable(publish_chat):
            try:
                publish_chat(effective_req_id, normalized_text)
            except Exception:
                _log.debug("evt=static_response_history_failed sid=%s", sid, exc_info=True)

    def _prepare_llm_history(
        self,
        sid: str,
        session: _TurnSession,
        req_id: str,
        *,
        extra_system_instruction: Optional[str] = None,
        injected_messages: Optional[Sequence[Mapping[str, Any]]] = None,
    ) -> None:
        history: list[Mapping[str, Any]] = []
        try:
            history = self._conversation_buffer.messages(sid)
        except Exception:
            history = []

        trimmed: list[Mapping[str, Any]] = []
        if history:
            trimmed = [item for item in history[-6:] if isinstance(item, Mapping)]

        sanitized: list[Dict[str, str]] = []
        for entry in trimmed:
            role = entry.get("role")
            text = entry.get("text")
            if not isinstance(role, str) or not isinstance(text, str):
                continue
            cleaned = text.strip()
            if not cleaned:
                continue
            sanitized.append({"role": role, "text": cleaned[:512]})

        if isinstance(extra_system_instruction, str):
            instruction_text = extra_system_instruction.strip()
            if instruction_text:
                sanitized.insert(0, {"role": "system", "text": instruction_text[:512]})

        if injected_messages:
            for message in injected_messages:
                if not isinstance(message, Mapping):
                    continue
                role = message.get("role")
                content = message.get("content")
                if not isinstance(role, str) or not isinstance(content, str):
                    continue
                cleaned = content.strip()
                if not cleaned:
                    continue
                sanitized.append({"role": role, "text": cleaned[:512]})

        session.history_message_count = len(sanitized)

        cache_history = getattr(self._llm, "cache_history", None)
        if callable(cache_history):
            try:
                cache_history(req_id, sanitized)
            except Exception:
                _log.debug(
                    "evt=llm_history_cache_failed sid=%s req_id=%s",
                    sid,
                    req_id,
                    exc_info=True,
                )

    def _emit_assistant_turn_begin(self, sid: str, session: _TurnSession) -> None:
        if session.assistant_turn_open:
            return

        turn_id = session.turn_id
        req_id = session.req_id
        if not isinstance(turn_id, str) or not turn_id:
            return
        if not isinstance(req_id, str) or not req_id:
            return

        frame = {
            "type": "turn.begin",
            "turn_id": turn_id,
            "req_id": req_id,
            "ts_ms": _now_ms(),
            "origin": "assistant",
            "reason": "assistant_llm_start",
        }
        serialized = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
        payload = {
            "meta": {
                "ws": {
                    "dir": "out",
                    "size": len(serialized.encode("utf-8")),
                    "preview": serialized,
                }
            },
            "frame": frame,
        }
        event = self._envelope(sid, EVT_WS_JSON_SEND, payload)
        self._publish(event)
        session.assistant_turn_open = True

    def _record_turn_timing(
        self, sid: str, session: _TurnSession, stage: str
    ) -> None:
        field_name: Optional[str]
        if stage == "asr_final":
            field_name = "metrics_asr_final_ms"
        elif stage == "llm_start":
            field_name = "metrics_llm_start_ms"
        elif stage == "llm_end":
            field_name = "metrics_llm_end_ms"
        elif stage == "tts_start":
            field_name = "metrics_tts_start_ms"
        elif stage == "tts_first_chunk":
            field_name = "metrics_tts_first_chunk_ms"
        elif stage == "tts_end":
            field_name = "metrics_tts_end_ms"
        else:
            return

        existing = getattr(session, field_name, None)
        if existing is not None:
            return

        timestamp = _now_ms()
        setattr(session, field_name, timestamp)
        self._log_turn_metrics(sid, session, stage)

    def _log_turn_metrics(self, sid: str, session: _TurnSession, stage: str) -> None:
        stages_logged = session.metrics_logged_stages
        if stage in stages_logged:
            return
        stages_logged.add(stage)

        parts = ["TURN_METRICS", f"stage={stage}", f"sid={sid}"]
        if isinstance(session.turn_id, str) and session.turn_id:
            parts.append(f"turn_id={session.turn_id}")
        if isinstance(session.req_id, str) and session.req_id:
            parts.append(f"req_id={session.req_id}")
        if session.history_message_count:
            parts.append(f"history_messages={session.history_message_count}")
        if isinstance(session.answer_chars, int):
            parts.append(f"answer_chars={session.answer_chars}")

        durations = (
            ("asr_to_llm_ms", self._safe_duration(session.metrics_asr_final_ms, session.metrics_llm_start_ms)),
            ("llm_ms", self._safe_duration(session.metrics_llm_start_ms, session.metrics_llm_end_ms)),
            (
                "llm_to_tts_ms",
                self._safe_duration(session.metrics_llm_end_ms, session.metrics_tts_start_ms),
            ),
            (
                "tts_first_chunk_ms",
                self._safe_duration(
                    session.metrics_tts_start_ms, session.metrics_tts_first_chunk_ms
                ),
            ),
            (
                "tts_total_ms",
                self._safe_duration(session.metrics_tts_start_ms, session.metrics_tts_end_ms),
            ),
        )

        for label, value in durations:
            if value is not None:
                parts.append(f"{label}={value}")

        _log.info(" ".join(parts), extra={"sid": sid, "event": "turn_metrics"})

    @staticmethod
    def _safe_duration(
        start_ms: Optional[int], end_ms: Optional[int]
    ) -> Optional[int]:
        if start_ms is None or end_ms is None:
            return None
        duration = end_ms - start_ms
        return duration if duration >= 0 else 0

    def _emit_policy_applied(
        self,
        sid: str,
        previous: Dict[str, Any] | None,
        diff: Dict[str, Dict[str, Any]],
    ) -> None:
        meta = {"policy": {"diff": self._summarize_policy_diff(previous, diff)}}
        event = self._envelope(sid, EVT_POLICY_APPLIED, {"meta": meta})
        self._publish(event)

    def _emit_acwr_breadcrumb(self, sid: str, snapshot: Dict[str, Any]) -> None:
        """Publish a breadcrumb describing the Auto-Commit-When-Ready state."""

        policy_acwr = (
            snapshot["auto_commit_when_ready"]
            if "auto_commit_when_ready" in snapshot
            else None
        )
        effective = bool(snapshot.get("auto_commit_when_ready", True))
        meta = {
            "policy_acwr": policy_acwr,
            "admin_enabled": None,
            "effective": effective,
        }
        event = self._envelope(sid, EVT_ACWR_RECOMPUTE, {"meta": meta})
        self._publish(event)

    @staticmethod
    def _summarize_policy_diff(
        previous: Dict[str, Any] | None,
        diff: Dict[str, Dict[str, Any]],
    ) -> Dict[str, list[Any]]:
        summary: Dict[str, list[Any]] = {}
        before = dict(previous or {})

        for key, value in diff.get("added", {}).items():
            summary[key] = [before.get(key), value]

        for key, value in diff.get("changed", {}).items():
            summary[key] = [before.get(key), value]

        for key, value in diff.get("removed", {}).items():
            summary[key] = [value, None]

        return summary


class VoiceEngine(EngineV2):
    """Voice engine with proactive barge-in cancellation support."""

    def __init__(
        self,
        exporter: FileExporter | None = None,
        *,
        telemetry_bus=bus,
        fake_exporter: FileExporter | None = None,
        tts_runtime: Any | None = None,
    ) -> None:
        super().__init__(
            exporter=exporter,
            telemetry_bus=telemetry_bus,
            fake_exporter=fake_exporter,
        )
        self.tts_runtime = tts_runtime
        self._llm_generator_task: asyncio.Task[Any] | None = None
        self._mic_open_tokens: Dict[str, tuple[Callable[[str], bool], str]] = {}

    async def on_open(self, sid: str, headers: Mapping[str, str]) -> None:
        maybe_coro = super().on_open(sid, headers)
        if inspect.isawaitable(maybe_coro):
            await maybe_coro

        self._unsubscribe_barge_in(sid)

        def _handler(event: dict, *, _sid=sid) -> None:
            self._on_client_mic_open(_sid, event)

        subscribe_func = getattr(self._bus, "subscribe", None)
        unsubscribe_func = getattr(self._bus, "unsubscribe", None)
        if callable(subscribe_func) and callable(unsubscribe_func):
            token = subscribe_func(EVT_CLIENT_MIC_OPEN, _handler)
            unsubscribe_cb: Callable[[str], bool] = unsubscribe_func
        else:
            token = bus.subscribe(EVT_CLIENT_MIC_OPEN, _handler)
            unsubscribe_cb = bus.unsubscribe

        self._mic_open_tokens[sid] = (unsubscribe_cb, token)

    async def on_close(self, sid: str, code: int, reason: Optional[str]) -> None:
        try:
            maybe_coro = super().on_close(sid, code, reason)
            if inspect.isawaitable(maybe_coro):
                await maybe_coro
        finally:
            self._unsubscribe_barge_in(sid)

    async def _handle_barge_in(
        self, sid: str, event: Mapping[str, Any] | None = None
    ) -> None:
        runtime = getattr(self, "tts_runtime", None)
        if not getattr(runtime, "is_active", False):
            return

        _log.info("evt=barge_in_detected sid=%s", sid, extra={"sid": sid, "event": "barge_in_detected"})

        llm_task = getattr(self, "_llm_generator_task", None)
        if isinstance(llm_task, asyncio.Task):
            if not llm_task.done():
                llm_task.cancel()
                try:
                    await llm_task
                except asyncio.CancelledError:
                    pass
                except Exception:  # pragma: no cover - defensive logging
                    _log.exception(
                        "evt=barge_in_llm_cancel_failed sid=%s",
                        sid,
                        extra={"sid": sid, "event": "barge_in_llm_cancel_failed"},
                    )
        self._llm_generator_task = None

        interrupt = getattr(runtime, "interrupt", None)
        if callable(interrupt):
            try:
                interrupt()
            except Exception:  # pragma: no cover - defensive logging
                _log.exception(
                    "evt=barge_in_tts_interrupt_failed sid=%s",
                    sid,
                    extra={"sid": sid, "event": "barge_in_tts_interrupt_failed"},
                )
            else:
                _log.info(
                    "evt=barge_in_tts_interrupted sid=%s",
                    sid,
                    extra={"sid": sid, "event": "barge_in_tts_interrupted"},
                )

    def _on_client_mic_open(self, sid: str, event: Mapping[str, Any] | None) -> None:
        runtime = getattr(self, "tts_runtime", None)
        if not getattr(runtime, "is_active", False):
            return
        if not isinstance(event, Mapping):
            return
        event_sid = event.get("sid")
        if not isinstance(event_sid, str) or event_sid != sid:
            return

        coroutine = self._handle_barge_in(sid, event)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            loop.create_task(coroutine)
        else:
            try:
                asyncio.run(coroutine)
            except RuntimeError:  # pragma: no cover - defensive logging
                _log.exception("evt=barge_in_task_schedule_failed sid=%s", sid)

    def _unsubscribe_barge_in(self, sid: str) -> None:
        entry = self._mic_open_tokens.pop(sid, None)
        if not entry:
            return
        unsubscribe_cb, token = entry
        try:
            unsubscribe_cb(token)
        except Exception:  # pragma: no cover - defensive logging
            _log.exception("evt=barge_in_unsubscribe_failed sid=%s", sid)


__all__ = [
    "EngineV2",
    "EVT_TURN_BEGIN",
    "EVT_TURN_END",
    "EVT_PERF_SUMMARY",
    "EVT_TURN_STATE",
    "READY",
    "LISTENING",
    "THINKING",
    "RESPONDING",
    "VoiceEngine",
]
