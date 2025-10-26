"""Minimal Engine v2 shell with telemetry hooks and session exporting."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import re
import threading
import time
import uuid
from typing import Any, Dict, Mapping, Optional

from app.logging_config import apply_logging_policy
from app.policy.loader import load_interaction_policy
from app.policy.watch import compute_diff, should_reapply
from app.telemetry import bus
from app.telemetry.exporter import FileExporter
from app.voice_v2 import (
    EVT_ACWR_RECOMPUTE,
    EVT_CHAT_USER,
    EVT_ASR_FINAL,
    EVT_ASR_PARTIAL,
    EVT_NLG,
    EVT_NLU,
    EVT_DIALOG_PLAN,
    EVT_POLICY_APPLIED,
    EVT_TTS_END,
    EVT_TTS_START,
    EVT_WS_AUDIO_RECV,
    EVT_WS_AUDIO_SEND,
    EVT_WS_CLOSE,
    EVT_WS_JSON_RECV,
    EVT_WS_JSON_SEND,
    EVT_WS_OPEN,
)
from app.voice_v2.nlu import NLUAdapter
from app.voice_v2.policy_decider import PolicyDecider, EVT_POLICY_DECISION
from app.voice_v2.llm import LLMAdapter
from app.voice_v2.gate import GateController
from app.voice_v2.conversation_buffer import ConversationBuffer
from app.voice_v2.vad import VADAggregator
from app.voice_v2.planner import _MODE_CHIPS, plan_turn
from app.voice_v2.persona import default_chips_for_mode, load_persona
from app.voice_v2.streaming import StreamingController


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

_PCM_CODEC = "pcm_s16le"
_PCM_RATE_HZ = 16000
_PCM_CHANNELS = 1
_PCM_DESCRIPTOR = {
    "codec": _PCM_CODEC,
    "rate_hz": _PCM_RATE_HZ,
    "channels": _PCM_CHANNELS,
}

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
EVT_LLM_REQUEST = "EVT_LLM_REQUEST"
EVT_LLM_COMPLETE = "EVT_LLM_COMPLETE"


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
    req_id: Optional[str] = None
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
        self._aggregators: Dict[str, VADAggregator] = {}
        self._streaming = StreamingController()
        self._conversation_buffer = ConversationBuffer()
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
        self._ensure_session(sid)
        self.reapply_policy()
        self._emit_info_frame(sid)
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
        previous_state = session.state
        policy = self.policy_snapshot or {}
        barge_enabled = bool(policy.get("barge_in_enabled"))

        if previous_state == RESPONDING:
            granted = barge_enabled
            reason = None if granted else "policy_disabled"
            self._publish_barge(sid, "text", granted, reason)
            if granted:
                self.cancel_current_tts(sid, reason="canceled")

        if previous_state != READY:
            reset_reason = "text_barge_reset" if (previous_state == RESPONDING and barge_enabled) else "text_input_reset"
            self._set_state(sid, READY, reason=reset_reason)

        listen_reason = "text_barge" if (previous_state == RESPONDING and barge_enabled) else "text_input"
        self._set_state(sid, LISTENING, reason=listen_reason)

        session = self._ensure_session(sid)
        turn_id = session.turn_id or str(uuid.uuid4())
        req_id = session.req_id or f"req-{uuid.uuid4().hex}"
        session.turn_id = turn_id
        session.req_id = req_id

        self._emit_user_chat_message(sid, text, turn_id, req_id, client_msg_id)
        self._set_state(sid, THINKING, reason="text_input")

    def on_audio(self, sid: str, chunk: bytes, seq: int) -> None:
        """Capture an incoming audio chunk."""
        session = self._ensure_session(sid)
        if session.state != LISTENING:
            self._set_state(sid, LISTENING, reason="audio_rx")
        byte_count = len(chunk)
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
        self._cancel_barge_confirmation(sid)
        self._aggregators.pop(sid, None)
        self._sessions.pop(sid, None)
        buffers = getattr(self._conversation_buffer, "_buffers", None)
        if isinstance(buffers, dict):
            buffers.pop(sid, None)

    def on_tts_start(
        self, sid: str, utt_id: str, post_hold_ms: int | None = None
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
        tts_meta = {
            "tts": {"utt_id": utt_id, "post_hold_ms": hold_ms},
            "voice_id": voice_id,
            "locale": locale,
        }
        self._gate.set_reason(
            "tts_active",
            True,
            sid=sid,
            meta=tts_meta,
        )

        self._publish_tts_mask(sid, "engaged")

        payload = {"meta": tts_meta}
        event = self._envelope(sid, EVT_TTS_START, payload)
        self._publish(event)
        self._set_state(sid, RESPONDING, reason="tts_start")

        aggregator = self._aggregators.get(sid)
        if aggregator is not None:
            aggregator.on_tts_start()

        self._streaming.set_output_finalizer(
            sid, lambda _sid=sid: self.cancel_current_tts(_sid, reason="ended")
        )

    def on_tts_end(
        self, sid: str, utt_id: str, post_hold_ms: int | None = None
    ) -> None:
        """Release the TTS mask and optionally engage a post-hold."""

        hold_ms = post_hold_ms or 0
        self._teardown_tts(sid, utt_id, post_hold_ms=hold_ms, transition_to_ready=True)

    def emit_tts_audio_chunk(self, sid: str, chunk: bytes | bytearray | memoryview) -> None:
        """Emit a PCM chunk for server-to-client playback."""

        if not isinstance(sid, str) or not sid:
            raise ValueError("sid must be a non-empty string")

        self._ensure_session(sid)

        if isinstance(chunk, (bytes, bytearray, memoryview)):
            pcm_chunk = bytes(chunk)
        else:  # pragma: no cover - defensive guard
            raise TypeError("chunk must be bytes-like")

        if not pcm_chunk:
            return

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

        session = self._ensure_session(sid)

        provided_req_id = req_id if isinstance(req_id, str) and req_id else None
        if provided_req_id is not None:
            session.req_id = provided_req_id

        if session.turn_started_ms is not None:
            elapsed = _now_ms() - session.turn_started_ms
            if elapsed < 0:
                elapsed = 0
            session.perf_final_ms = elapsed
        else:
            session.turn_started_ms = _now_ms()

        turn_id = session.turn_id or str(uuid.uuid4())
        session.turn_id = turn_id

        req_id = session.req_id or provided_req_id or f"req-{uuid.uuid4().hex}"
        session.req_id = req_id

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

            if not session.nlu_emitted:
                nlu_result = self._nlu.extract(req_id, text)
                nlu_payload = {"req_id": req_id, "turn_id": turn_id, **nlu_result}
                nlu_event = self._envelope(sid, EVT_NLU, nlu_payload)
                self._publish(nlu_event)
                session.nlu_emitted = True
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

        if session.turn_started_ms is None:
            session.turn_started_ms = _now_ms()

        if isinstance(req_id, str) and req_id:
            session.req_id = req_id
        else:
            req_id = session.req_id or f"req-{uuid.uuid4().hex}"
            session.req_id = req_id

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

        self._publish_barge(sid, source, granted, reason if granted else deny_reason)

        if granted:
            self.cancel_current_tts(sid, reason="canceled")
            self._set_state(sid, CONFIRMING_BARGE, reason="auto_barge")
            self._schedule_barge_confirmation(sid)
        else:
            _log.info(
                "evt=barge_auto_denied reason=%s source=%s",
                deny_reason,
                source,
                extra={"sid": sid},
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

            policy_payload = {"rule": "session_open.greet", "actions": ["assistant.say"]}
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
                "purpose": "greet",
                "model": model_name,
                "temp": temperature,
                "max_tokens": max_tokens,
                "msg_counts": msg_counts,
            }
            request_event = self._envelope(sid, EVT_LLM_REQUEST, request_payload)
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
                    "purpose": "greet",
                    "latency_ms": latency_ms,
                    "model": model_name,
                }
                complete_event = self._envelope(
                    sid, EVT_LLM_COMPLETE, complete_payload
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

    def _publish_barge(
        self, sid: str, source: str, granted: bool, reason: str | None = None
    ) -> None:
        """Publish EVT_BARGE_IN envelope with meta.barge.{source,granted,reason}."""

        if source not in {"auto_vad", "asr_evidence", "text"}:
            return

        meta: Dict[str, Any] = {"barge": {"source": source, "granted": granted}}
        if reason is not None:
            meta["barge"]["reason"] = reason
        event = self._envelope(sid, "EVT_BARGE_IN", {"meta": meta})
        self._publish(event)

    def _schedule_barge_confirmation(self, sid: str) -> None:
        """Transition from confirming to listening after a short delay."""

        async def _confirm() -> None:
            await asyncio.sleep(0.5)
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
            timer = threading.Timer(0.5, self._complete_auto_barge, args=(sid,))
            timer.daemon = True
            timer.start()
            self._barge_handles[sid] = timer

    def _cancel_barge_confirmation(self, sid: str) -> None:
        handle = self._barge_handles.pop(sid, None)
        if handle is None:
            return
        if isinstance(handle, asyncio.Task) and not handle.done():
            handle.cancel()
        elif isinstance(handle, threading.Timer):
            handle.cancel()

    def _complete_auto_barge(self, sid: str) -> None:
        self._barge_handles.pop(sid, None)
        self._set_state(sid, LISTENING, reason="auto_barge_confirmed")
        self._gate.set_reason("tts_active", False, sid=sid)

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

        aggregator = VADAggregator(sid, self._bus, _policy_supplier)
        aggregator.set_grant_handler(
            lambda source, info, *, _sid=sid: self._handle_vad_grant(_sid, source, info)
        )
        self._aggregators[sid] = aggregator

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
            return

        now_ms = _now_ms()

        if new_state == LISTENING:
            session.turn_id = str(uuid.uuid4())
            session.req_id = f"req-{uuid.uuid4().hex}"
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
            begin_payload = {
                "turn_id": session.turn_id,
                "req_id": session.req_id,
                "meta": {
                    "turn_id": session.turn_id,
                    "req_id": session.req_id,
                    "state": LISTENING,
                },
            }
            begin_event = self._envelope(sid, EVT_TURN_BEGIN, begin_payload)
            self._publish(begin_event)

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

    def turn_context(self, sid: str) -> Optional[Dict[str, str]]:
        """Return the active turn context for ``sid`` if a turn is in progress."""

        session = self._sessions.get(sid)
        if (
            session is None
            or session.turn_id is None
            or session.req_id is None
            or session.state == READY
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

    def _publish_tts_mask(self, sid: str, phase: str) -> None:
        mask_event = self._envelope(sid, "EVT_TTS_MASK", {"phase": phase})
        self._publish(mask_event)

    def _teardown_tts(
        self,
        sid: str,
        utt_id: str,
        *,
        reason: str | None = None,
        post_hold_ms: int = 0,
        transition_to_ready: bool,
    ) -> None:
        self._streaming.set_output_finalizer(sid, None)
        session = self._ensure_session(sid)
        if session.tts_utt_id == utt_id:
            session.tts_utt_id = None

        aggregator = self._aggregators.get(sid)
        if aggregator is not None:
            aggregator.on_tts_end()

        payload: Dict[str, Any] = {"meta": {"tts": {"utt_id": utt_id}}}
        if reason:
            payload["reason"] = reason

        event = self._envelope(sid, EVT_TTS_END, payload)
        self._publish(event)

        self._publish_tts_mask(sid, "cleared")
        self._gate.set_reason("tts_active", False, sid=sid, meta={"tts": {"utt_id": utt_id}})

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

    def _emit_policy_frame(self, sid: str, snapshot: Dict[str, Any]) -> None:
        frame = {"type": "policy.interaction", "policy": snapshot}
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

    def _maybe_emit_policy_and_nlg(
        self,
        sid: str,
        session: _TurnSession,
        nlu_payload: Mapping[str, Any],
    ) -> None:
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

        if decision.get("action") != "respond" or session.nlg_emitted:
            return

        intent = nlu_payload.get("intent")
        if not isinstance(intent, str) or not intent:
            intent = "chitchat.fallback"

        entities = nlu_payload.get("entities")
        if isinstance(entities, Mapping):
            entity_payload = dict(entities)
        else:
            entity_payload = {}

        plan_context = session.plan if isinstance(session.plan, Mapping) else None
        plan_mode = None
        if plan_context is not None:
            plan_req_id = plan_context.get("req_id")
            if plan_req_id == req_id:
                candidate_mode = plan_context.get("mode")
                if isinstance(candidate_mode, str) and candidate_mode:
                    plan_mode = candidate_mode

        provider = getattr(self._llm, "_provider", None)
        model_name = getattr(provider, "default_model", None)
        request_payload: Dict[str, Any] = {"purpose": "answer"}
        if intent:
            request_payload["intent"] = intent
        if plan_mode:
            request_payload["mode"] = plan_mode
        if entity_payload:
            request_payload["entity_count"] = len(entity_payload)

        request_event = self._envelope(sid, EVT_LLM_REQUEST, request_payload)
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
            )
        else:
            llm_result = self._llm.generate(req_id, intent=intent, entities=entity_payload)
            if isinstance(llm_result, Mapping):
                response_text = llm_result.get("text")
            else:
                response_text = llm_result

        latency_ms = max(int((time.perf_counter() - llm_start) * 1000), 0)
        complete_payload: Dict[str, Any] = {"purpose": "answer", "latency_ms": latency_ms}
        if isinstance(model_name, str) and model_name:
            complete_payload["model"] = model_name
        complete_event = self._envelope(sid, EVT_LLM_COMPLETE, complete_payload)
        self._publish(complete_event)

        if not isinstance(response_text, str):
            response_text = str(response_text)

        nlg_payload = {"req_id": req_id, "text": response_text}
        nlg_event = self._envelope(sid, EVT_NLG, nlg_payload)
        self._publish(nlg_event)
        session.nlg_emitted = True

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
]
