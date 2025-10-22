"""Dual-VAD aggregator with adaptive policy controls."""
from __future__ import annotations

import math
import time
from array import array
from typing import Callable, Dict, List, Mapping, MutableMapping, Optional


def rms_dbfs_from_pcm16(samples: bytes) -> float:
    """Return the RMS level in dBFS for 16-bit PCM samples."""

    if not samples:
        return -96.0

    pcm = array("h")
    pcm.frombytes(samples)
    if not pcm:
        return -96.0

    total = 0.0
    for value in pcm:
        total += float(value * value)

    rms = math.sqrt(total / len(pcm)) if pcm else 0.0
    if rms <= 0.0:
        return -96.0

    peak = float(1 << 15)
    dbfs = 20.0 * math.log10(rms / peak)
    return max(-96.0, min(0.0, dbfs))


class VADAggregator:
    """Fuse auto energy and ASR evidence into a single decision stream."""

    DEFAULTS: Mapping[str, float | int | str] = {
        "mode": "or",
        "priority": "asr",
        "min_speech_ms": 200,
        "energy_threshold_dbfs": -45.0,
        "hold_ms": 200,
        "echo_suppression_ms": 350,
        "barge_cooldown_ms": 250,
        "asr_conf_threshold": 0.75,
    }

    MIN_MARGIN_DB = 10.0
    MAX_MARGIN_DB = 18.0
    NOISE_ALPHA = 0.05

    def __init__(
        self,
        sid: str,
        bus,
        policy_supplier: Callable[[], MutableMapping[str, object] | Mapping[str, object] | None],
    ) -> None:
        self._sid = sid
        self._bus = bus
        self._policy_supplier = policy_supplier
        self._policy: Dict[str, float | int | str] = {}

        self._engine_mode: Optional[str] = None
        self._in_tts = False
        self._echo_suppressed_until_ms = 0
        self._grant_locked = False
        self._last_grant_ms = 0

        self._auto_contiguous_ms = 0
        self._auto_inactive_ms = 0
        self._auto_active_duration_ms = 0
        self._auto_active = False
        self._auto_last_dbfs = -96.0

        self._asr_active = False
        self._asr_confidence = 0.0
        self._asr_last_update_ms = 0

        baseline = float(self.DEFAULTS["energy_threshold_dbfs"])
        self._margin_db = (self.MIN_MARGIN_DB + self.MAX_MARGIN_DB) / 2.0
        self._nf_dbfs = baseline - self._margin_db
        self._environment = "normal"
        self._false_positive_events: List[int] = []

        self._grant_handler: Optional[Callable[[str, Dict[str, object]], None]] = None

        self._refresh_policy()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_grant_handler(self, handler: Callable[[str, Dict[str, object]], None]) -> None:
        self._grant_handler = handler

    def feed_auto_energy(self, dbfs: float, frame_ms: int = 20) -> None:
        self._refresh_policy()
        now_ms = self._now_ms()
        self._expire_asr(now_ms)

        threshold = self.dynamic_threshold_dbfs()
        effective_mode, _, min_speech_ms = self._effective_settings()

        suppressed = self._in_tts and now_ms < self._echo_suppressed_until_ms

        if dbfs < threshold:
            self._update_noise_floor(dbfs)

        if suppressed:
            if self._auto_active:
                self._auto_inactive_ms += frame_ms
                self._auto_active_duration_ms += frame_ms
                if self._auto_inactive_ms >= self._policy_hold_ms():
                    self._set_auto_active(False, dbfs)
            return

        if dbfs >= threshold:
            self._auto_contiguous_ms += frame_ms
            self._auto_inactive_ms = 0
            if not self._auto_active and self._auto_contiguous_ms >= min_speech_ms:
                self._auto_active_duration_ms = self._auto_contiguous_ms
                self._set_auto_active(True, dbfs)
            elif self._auto_active:
                self._auto_active_duration_ms += frame_ms
        else:
            if self._auto_active:
                self._auto_active_duration_ms += frame_ms
                self._auto_inactive_ms += frame_ms
                if self._auto_inactive_ms >= self._policy_hold_ms():
                    self._set_auto_active(False, dbfs)
            self._auto_contiguous_ms = 0

        self._attempt_grant("auto_vad", effective_mode, now_ms)

    def feed_asr_evidence(
        self,
        req_id: str,
        confidence: float,
        partial_text: Optional[str] = None,
    ) -> None:
        self._refresh_policy()
        now_ms = self._now_ms()
        self._expire_asr(now_ms)

        threshold = float(self._policy["asr_conf_threshold"])
        if confidence >= threshold:
            if not self._asr_active:
                self._set_asr_active(True, confidence)
            self._asr_confidence = confidence
            self._asr_last_update_ms = now_ms
        else:
            if self._asr_active:
                self._set_asr_active(False, confidence)
            self._asr_confidence = confidence
            self._asr_last_update_ms = now_ms

        effective_mode, _, _ = self._effective_settings()
        self._attempt_grant("asr_evidence", effective_mode, now_ms)

    def on_tts_start(self) -> None:
        self._refresh_policy()
        now_ms = self._now_ms()
        self._in_tts = True
        self._echo_suppressed_until_ms = now_ms + int(self._policy["echo_suppression_ms"])
        self._grant_locked = False
        self._auto_contiguous_ms = 0
        self._auto_inactive_ms = 0

    def on_tts_end(self) -> None:
        self._in_tts = False
        self._echo_suppressed_until_ms = 0
        self._grant_locked = False
        self._auto_contiguous_ms = 0
        self._auto_inactive_ms = 0
        if self._auto_active:
            self._set_auto_active(False, self._auto_last_dbfs)

    def on_engine_mode_change(self, mode: str) -> None:
        self._engine_mode = mode
        if mode not in {"AssistantSpeaking", "Responding", "RESPONDING"}:
            self._grant_locked = False

    def dynamic_threshold_dbfs(self) -> float:
        baseline = float(self._policy["energy_threshold_dbfs"])
        return max(baseline, self._nf_dbfs + self._margin_db)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _now_ms(self) -> int:
        return int(time.monotonic() * 1000)

    def _refresh_policy(self) -> None:
        snapshot: Mapping[str, object] | None
        try:
            snapshot = self._policy_supplier() or {}
        except Exception:  # pragma: no cover - defensive
            snapshot = {}

        vad_block: Mapping[str, object] = {}
        if isinstance(snapshot, Mapping):
            maybe_vad = snapshot.get("vad")
            if isinstance(maybe_vad, Mapping):
                vad_block = maybe_vad

        combined: Dict[str, float | int | str] = dict(self.DEFAULTS)
        for key, value in vad_block.items():
            if key in combined:
                combined[key] = value

        if combined == self._policy:
            return

        self._policy = combined
        baseline = float(combined["energy_threshold_dbfs"])
        if self._nf_dbfs + self._margin_db < baseline:
            self._nf_dbfs = baseline - self._margin_db

    def _policy_hold_ms(self) -> int:
        return int(self._policy["hold_ms"])

    def _effective_settings(self) -> tuple[str, str, int]:
        mode = str(self._policy["mode"]).lower()
        priority = str(self._policy["priority"]).lower()
        min_speech_ms = int(self._policy["min_speech_ms"])

        self._prune_false_positive_events(self._now_ms())

        nf = self._nf_dbfs
        margin = self._margin_db
        environment = "normal"
        if nf > -38 or len(self._false_positive_events) >= 2:
            environment = "noisy"
        elif nf < -60 and not self._false_positive_events:
            environment = "quiet"

        self._environment = environment

        if environment == "noisy":
            min_speech_ms = min(500, max(200, min_speech_ms + 100))
        elif environment == "quiet":
            if mode == "or":
                min_speech_ms = max(200, int(self.DEFAULTS["min_speech_ms"]))
            else:
                min_speech_ms = max(200, min_speech_ms)

        return mode, priority, min_speech_ms

    def _expire_asr(self, now_ms: int) -> None:
        if not self._asr_active:
            return
        timeout = max(self._policy_hold_ms(), 400)
        if now_ms - self._asr_last_update_ms >= timeout:
            self._set_asr_active(False, self._asr_confidence)

    def _set_auto_active(self, active: bool, dbfs: float) -> None:
        if self._auto_active == active:
            return

        self._auto_active = active
        self._auto_last_dbfs = dbfs
        phase = "start" if active else "stop"
        payload = {
            "type": "EVT_VAD",
            "sid": self._sid,
            "phase": phase,
            "metric": {"dbfs": float(dbfs), "active_ms": int(self._auto_active_duration_ms)},
            "who": "server",
            "source": "auto_vad",
        }
        self._bus.publish(payload)
        if not active:
            self._auto_active_duration_ms = 0

    def _set_asr_active(self, active: bool, confidence: float) -> None:
        if self._asr_active == active and active:
            return

        self._asr_active = active
        phase = "start" if active else "stop"
        payload = {
            "type": "EVT_VAD",
            "sid": self._sid,
            "phase": phase,
            "metric": {"confidence": float(confidence)},
            "who": "server",
            "source": "asr_evidence",
        }
        self._bus.publish(payload)

    def _attempt_grant(self, trigger: str, effective_mode: str, now_ms: int) -> None:
        reasons: List[str] = []

        allowed_modes = {"AssistantSpeaking", "Responding", "RESPONDING"}
        if self._engine_mode not in allowed_modes:
            reasons.append("engine_not_assistant_speaking")

        cooldown_ms = int(self._policy["barge_cooldown_ms"])
        in_cooldown = now_ms - self._last_grant_ms < cooldown_ms
        if in_cooldown:
            reasons.append("cooldown_active")

        if self._grant_locked:
            reasons.append("single_grant_lock")

        if self._in_tts and now_ms < self._echo_suppressed_until_ms:
            reasons.append("echo_suppression_active")

        auto_active = self._auto_active
        asr_active = self._asr_active

        reasons.append("auto_vad_active" if auto_active else "auto_vad_inactive")
        reasons.append("asr_evidence_active" if asr_active else "asr_evidence_inactive")

        granted = False
        grant_source = trigger

        if not self._in_tts:
            reasons.append("tts_inactive")
        else:
            if effective_mode == "and":
                granted = auto_active and asr_active
                if granted:
                    grant_source = "auto_vad" if trigger == "auto_vad" else "asr_evidence"
            elif effective_mode == "or":
                if auto_active or asr_active:
                    grant_source = "auto_vad" if auto_active else "asr_evidence"
                    granted = True
            elif effective_mode == "priority":
                priority = str(self._policy["priority"]).lower()
                if priority == "auto":
                    granted = auto_active
                    grant_source = "auto_vad"
                else:
                    granted = asr_active
                    grant_source = "asr_evidence"

        if reasons and granted:
            reasons.append("mode:" + effective_mode)

        if granted:
            if in_cooldown or self._grant_locked:
                granted = False
                reasons.append("grant_blocked")

        if granted and self._engine_mode not in allowed_modes:
            granted = False

        decision_payload = {
            "type": "EVT_VAD_DECISION",
            "sid": self._sid,
            "mode": effective_mode,
            "granted": bool(granted),
            "reasons": list(reasons),
            "nf_dbfs": float(self._nf_dbfs),
            "margin_db": float(self._margin_db),
            "environment": self._environment,
            "who": "server",
            "source": "policy",
        }
        self._bus.publish(decision_payload)

        if not granted:
            if asr_active and not auto_active and effective_mode in {"and", "priority"}:
                self._register_miss()
            return

        self._grant_locked = True
        self._last_grant_ms = now_ms

        if grant_source == "auto_vad" and self._asr_confidence < float(self._policy["asr_conf_threshold"]) - 0.1:
            self._register_false_positive(now_ms)

        if self._grant_handler is not None:
            self._grant_handler(grant_source, {"mode": effective_mode, "reasons": reasons})

    def _register_false_positive(self, now_ms: int) -> None:
        self._margin_db = min(self.MAX_MARGIN_DB, self._margin_db + 1.5)
        self._false_positive_events.append(now_ms)

    def _register_miss(self) -> None:
        self._margin_db = max(self.MIN_MARGIN_DB, self._margin_db - 1.0)

    def _prune_false_positive_events(self, now_ms: int) -> None:
        window_ms = 5000
        self._false_positive_events = [ts for ts in self._false_positive_events if now_ms - ts < window_ms]

    def _update_noise_floor(self, dbfs: float) -> None:
        self._nf_dbfs = (1.0 - self.NOISE_ALPHA) * self._nf_dbfs + self.NOISE_ALPHA * float(dbfs)

