from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

from app.voice_v2 import (
    EVT_ACTION_SAY_END,
    EVT_ASR_FINAL,
    EVT_LLM_RESPONSE_END,
    EVT_LLM_RESPONSE_START,
    EVT_TTS_END,
    EVT_TTS_START,
    EVT_TURN_BEGIN,
    EVT_WS_AUDIO_SEND,
    EVT_TURN_ROLLUP,
)

_PCM_BYTES_PER_MS = 32  # 16 kHz, 16-bit mono => 32 bytes per millisecond


@dataclass
class _TurnMetrics:
    """Accumulate telemetry details for a single turn."""

    turn_id: Optional[str] = None
    turn_begin_ts: Optional[int] = None
    asr_final_ts: Optional[int] = None
    llm_start_ts: Optional[int] = None
    llm_end_ts: Optional[int] = None
    llm_latency_ms: Optional[int] = None
    tts_start_ts: Optional[int] = None
    tts_end_ts: Optional[int] = None
    audio_bytes: int = 0
    audio_total_bytes: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_est: float = 0.0
    interruption_reason: Optional[str] = None
    utt_id: Optional[str] = None
    emitted: bool = False


class TurnRollupAggregator:
    """Observe turn telemetry and publish EVT_TURN_ROLLUP once per reply."""

    def __init__(self, telemetry_bus) -> None:
        self._bus = telemetry_bus
        self._metrics: Dict[str, Dict[str, _TurnMetrics]] = {}
        self._active_req_by_sid: Dict[str, str] = {}
        self._last_req_by_sid: Dict[str, str] = {}
        self._emitted: set[Tuple[str, str]] = set()
        self._subscriptions: list[str] = []

        subscribe = getattr(self._bus, "subscribe", None)
        if callable(subscribe):
            self._subscriptions.extend(
                [
                    subscribe(EVT_TURN_BEGIN, self._handle_turn_begin),
                    subscribe(EVT_ASR_FINAL, self._handle_asr_final),
                    subscribe(EVT_LLM_RESPONSE_START, self._handle_llm_start),
                    subscribe(EVT_LLM_RESPONSE_END, self._handle_llm_end),
                    subscribe(EVT_TTS_START, self._handle_tts_start),
                    subscribe(EVT_TTS_END, self._handle_tts_end),
                    subscribe(EVT_WS_AUDIO_SEND, self._handle_audio_chunk),
                    subscribe(EVT_ACTION_SAY_END, self._handle_say_end),
                ]
            )

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------
    def clear_session(self, sid: str) -> None:
        """Remove any cached state for ``sid`` (e.g., when the session closes)."""

        if not isinstance(sid, str) or not sid:
            return
        self._metrics.pop(sid, None)
        self._active_req_by_sid.pop(sid, None)
        self._last_req_by_sid.pop(sid, None)
        self._emitted = {key for key in self._emitted if key[0] != sid}

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------
    def _handle_turn_begin(self, event: Mapping[str, Any]) -> None:
        sid, req_id = self._extract_ids(event)
        if not sid or not req_id:
            return
        metrics = self._ensure_metrics(sid, req_id)
        metrics.turn_id = self._coerce_str(event.get("turn_id"), metrics.turn_id)
        metrics.turn_begin_ts = self._coerce_ms(event.get("ts_ms"), metrics.turn_begin_ts)

    def _handle_asr_final(self, event: Mapping[str, Any]) -> None:
        sid, req_id = self._extract_ids(event)
        if not sid or not req_id:
            return
        metrics = self._ensure_metrics(sid, req_id)
        metrics.turn_id = self._coerce_str(event.get("turn_id"), metrics.turn_id)
        metrics.asr_final_ts = self._coerce_ms(event.get("ts_ms"), metrics.asr_final_ts)

    def _handle_llm_start(self, event: Mapping[str, Any]) -> None:
        sid, req_id = self._extract_ids(event)
        if not sid or not req_id:
            return
        metrics = self._ensure_metrics(sid, req_id)
        metrics.llm_start_ts = self._coerce_ms(event.get("ts_ms"), metrics.llm_start_ts)

    def _handle_llm_end(self, event: Mapping[str, Any]) -> None:
        sid, req_id = self._extract_ids(event)
        if not sid or not req_id:
            return
        metrics = self._ensure_metrics(sid, req_id)
        metrics.llm_end_ts = self._coerce_ms(event.get("ts_ms"), metrics.llm_end_ts)
        latency = self._coerce_int(event.get("latency_ms"))
        if latency is not None:
            metrics.llm_latency_ms = latency

        usage = event.get("usage")
        if isinstance(usage, Mapping):
            prompt = self._coerce_int(
                usage.get("input_tokens")
                or usage.get("prompt_tokens")
                or usage.get("prompt")
            )
            completion = self._coerce_int(
                usage.get("output_tokens")
                or usage.get("completion_tokens")
                or usage.get("completion")
            )
            cost_est = self._coerce_float(usage.get("cost_est") or usage.get("cost"))
            if prompt is not None:
                metrics.input_tokens = prompt
            if completion is not None:
                metrics.output_tokens = completion
            if cost_est is not None:
                metrics.cost_est = cost_est

        input_tokens = self._coerce_int(event.get("input_tokens"))
        output_tokens = self._coerce_int(event.get("output_tokens"))
        cost_est = self._coerce_float(event.get("cost_est"))
        if input_tokens is not None:
            metrics.input_tokens = input_tokens
        if output_tokens is not None:
            metrics.output_tokens = output_tokens
        if cost_est is not None:
            metrics.cost_est = cost_est

    def _handle_tts_start(self, event: Mapping[str, Any]) -> None:
        sid, req_id = self._extract_ids(event)
        if not sid or not req_id:
            return
        metrics = self._ensure_metrics(sid, req_id)
        metrics.tts_start_ts = self._coerce_ms(event.get("ts_ms"), metrics.tts_start_ts)
        utt_id = self._extract_utt_id(event.get("meta"))
        if not utt_id:
            utt_id = self._coerce_str(event.get("utt_id"), metrics.utt_id)
        if utt_id:
            metrics.utt_id = utt_id
        self._active_req_by_sid[sid] = req_id
        metrics.audio_bytes = 0
        metrics.audio_total_bytes = 0

    def _handle_tts_end(self, event: Mapping[str, Any]) -> None:
        sid, req_id = self._extract_ids(event)
        if not sid:
            return
        if not req_id:
            req_id = self._active_req_by_sid.get(sid)
            if not req_id:
                return
        metrics = self._ensure_metrics(sid, req_id)
        metrics.tts_end_ts = self._coerce_ms(event.get("ts_ms"), metrics.tts_end_ts)
        total_bytes = self._coerce_int(event.get("total_bytes"))
        if total_bytes is not None and total_bytes > metrics.audio_total_bytes:
            metrics.audio_total_bytes = total_bytes
        utt_id = self._extract_utt_id(event.get("meta"))
        if not utt_id:
            utt_id = self._coerce_str(event.get("utt_id"), metrics.utt_id)
        if utt_id:
            metrics.utt_id = utt_id
        self._active_req_by_sid.pop(sid, None)

    def _handle_audio_chunk(self, event: Mapping[str, Any]) -> None:
        sid = self._coerce_str(event.get("sid"))
        if not sid:
            return
        req_id = self._active_req_by_sid.get(sid)
        if not req_id:
            return
        metrics = self._ensure_metrics(sid, req_id)
        chunk_bytes = self._coerce_int(event.get("bytes"))
        if chunk_bytes is not None and chunk_bytes > 0:
            metrics.audio_bytes += chunk_bytes
            if metrics.audio_bytes > metrics.audio_total_bytes:
                metrics.audio_total_bytes = metrics.audio_bytes
        utt_id = self._coerce_str(event.get("utt_id"))
        if utt_id:
            metrics.utt_id = utt_id

    def _handle_say_end(self, event: Mapping[str, Any]) -> None:
        sid, req_id = self._extract_ids(event)
        if not sid:
            return
        if not req_id:
            req_id = self._last_req_by_sid.get(sid)
            if not req_id:
                return
        if (sid, req_id) in self._emitted:
            return
        metrics = self._metrics.get(sid, {}).get(req_id)
        if metrics is None:
            metrics = self._ensure_metrics(sid, req_id)
        if metrics.emitted:
            return

        metrics.interruption_reason = self._coerce_str(event.get("reason"))
        payload = self._build_rollup_payload(sid, req_id, metrics)
        metrics.emitted = True
        self._emitted.add((sid, req_id))
        self._publish(payload)
        metrics_by_sid = self._metrics.get(sid)
        if metrics_by_sid is not None:
            metrics_by_sid.pop(req_id, None)
            if not metrics_by_sid:
                self._metrics.pop(sid, None)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _publish(self, payload: Dict[str, Any]) -> None:
        if hasattr(self._bus, "publish"):
            self._bus.publish(payload)

    def _build_rollup_payload(
        self, sid: str, req_id: str, metrics: _TurnMetrics
    ) -> Dict[str, Any]:
        turn_id = metrics.turn_id
        asr_ms = self._duration(metrics.turn_begin_ts, metrics.asr_final_ts)
        llm_ms = metrics.llm_latency_ms
        if llm_ms is None:
            llm_ms = self._duration(metrics.llm_start_ts, metrics.llm_end_ts)
        tts_ms = self._duration(metrics.tts_start_ts, metrics.tts_end_ts)
        total_bytes = metrics.audio_total_bytes or metrics.audio_bytes
        speech_ms = 0
        if total_bytes > 0:
            speech_ms = max(total_bytes // _PCM_BYTES_PER_MS, 0)

        interruption = False
        reason = metrics.interruption_reason
        if reason and reason.lower() != "ended":
            interruption = True

        rollup: Dict[str, Any] = {
            "type": EVT_TURN_ROLLUP,
            "sid": sid,
            "req_id": req_id,
            "turn_id": turn_id,
            "asr_ms": asr_ms if asr_ms is not None else 0,
            "llm_ms": llm_ms if llm_ms is not None else 0,
            "tts_ms": tts_ms if tts_ms is not None else 0,
            "speech_ms": speech_ms,
            "input_tokens": metrics.input_tokens,
            "output_tokens": metrics.output_tokens,
            "cost_est": round(metrics.cost_est, 6),
            "interruption_during_tts": interruption,
        }
        if reason:
            rollup["reason"] = reason
        if turn_id is None:
            rollup.pop("turn_id", None)
        return rollup

    def _ensure_metrics(self, sid: str, req_id: str) -> _TurnMetrics:
        metrics_by_req = self._metrics.setdefault(sid, {})
        metrics = metrics_by_req.get(req_id)
        if metrics is None:
            metrics = _TurnMetrics()
            metrics_by_req[req_id] = metrics
        self._last_req_by_sid[sid] = req_id
        return metrics

    @staticmethod
    def _extract_ids(event: Mapping[str, Any]) -> Tuple[Optional[str], Optional[str]]:
        sid = event.get("sid")
        req_id = event.get("req_id")
        return (
            sid if isinstance(sid, str) and sid else None,
            req_id if isinstance(req_id, str) and req_id else None,
        )

    @staticmethod
    def _extract_utt_id(meta: Any) -> Optional[str]:
        if isinstance(meta, Mapping):
            tts = meta.get("tts")
            if isinstance(tts, Mapping):
                utt_id = tts.get("utt_id")
                if isinstance(utt_id, str) and utt_id:
                    return utt_id
        return None

    @staticmethod
    def _duration(start_ms: Optional[int], end_ms: Optional[int]) -> Optional[int]:
        if start_ms is None or end_ms is None:
            return None
        duration = end_ms - start_ms
        return duration if duration >= 0 else 0

    @staticmethod
    def _coerce_ms(candidate: Any, current: Optional[int] = None) -> Optional[int]:
        value = TurnRollupAggregator._coerce_int(candidate)
        if value is None:
            return current
        return value if value >= 0 else 0

    @staticmethod
    def _coerce_int(candidate: Any) -> Optional[int]:
        if isinstance(candidate, bool):
            return int(candidate)
        try:
            value = int(candidate)
        except (TypeError, ValueError):
            return None
        return value

    @staticmethod
    def _coerce_float(candidate: Any) -> Optional[float]:
        if candidate is None:
            return None
        try:
            value = float(candidate)
        except (TypeError, ValueError):
            return None
        if value != value:  # NaN check
            return None
        return value

    @staticmethod
    def _coerce_str(candidate: Any, fallback: Optional[str] = None) -> Optional[str]:
        if isinstance(candidate, str) and candidate:
            return candidate
        return fallback


__all__ = ["TurnRollupAggregator"]
