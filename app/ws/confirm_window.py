"""Utility for gating local barge-in confirmation."""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class ConfirmDecision:
    action: Optional[str]
    metrics: Optional[Dict[str, object]]


class ConfirmWindow:
    """Stateful helper that validates mic activity before committing barge-in."""

    def __init__(
        self,
        *,
        min_duration_ms: int = 420,
        max_duration_ms: int = 900,
        max_gap_ms: float = 180.0,
        min_tokens: int = 2,
        min_confidence: float = 0.5,
        snr_threshold_db: float = 8.0,
        snr_slack_db: float = 0.5,
        snr_enabled: bool = True,
    ) -> None:
        self.min_duration_ms = max(0, int(min_duration_ms))
        max_dur = int(max_duration_ms)
        if max_dur <= 0:
            max_dur = self.min_duration_ms + 600
        if max_dur < self.min_duration_ms:
            max_dur = self.min_duration_ms
        self.max_duration_ms = max_dur
        self._initial_window_span = self.max_duration_ms - self.min_duration_ms
        self.max_gap_ms = max(0.0, float(max_gap_ms))
        self.min_tokens = max(1, int(min_tokens))
        self.min_confidence = float(min_confidence)
        self.snr_threshold_db = float(snr_threshold_db)
        self.snr_slack_db = max(0.0, float(snr_slack_db))
        self.snr_enabled = bool(snr_enabled)

        self._base_snr_floor = max(0.0, self.snr_threshold_db - self.snr_slack_db)
        self._max_extension_used = False
        self._last_snr_floor: Optional[float] = None

        self.active = False
        self.start_ts = 0.0
        self.last_chunk_ts = 0.0
        self.noise_rms: Optional[float] = None
        self.peak_rms = 0.0
        self.snr_db: Optional[float] = None
        self.partial_tokens = 0
        self.partial_confidence: Optional[float] = None
        self.partial_ts = 0.0
        self.total_bytes = 0
        self.gap_grace_used = False
        self._max_extension_used = False
        self._last_snr_floor = None

    def set_snr_enabled(self, enabled: bool) -> None:
        self.snr_enabled = bool(enabled)

    def start(self, now_ts: float) -> None:
        self.active = True
        self.start_ts = now_ts
        self.last_chunk_ts = now_ts
        self.noise_rms = None
        self.peak_rms = 0.0
        self.snr_db = None
        self.partial_tokens = 0
        self.partial_confidence = None
        self.partial_ts = 0.0
        self.total_bytes = 0
        self.gap_grace_used = False
        self._max_extension_used = False
        self._last_snr_floor = None

    # ------------------------------- Public API -------------------------------

    def observe_chunk(self, chunk: bytes, now_ts: float) -> ConfirmDecision:
        if not self.active:
            return ConfirmDecision(None, None)

        elapsed_ms = self._elapsed_ms(now_ts)
        gap_decision = self._check_gap(now_ts)
        if gap_decision.action:
            return gap_decision

        self.last_chunk_ts = now_ts
        self.total_bytes += len(chunk or b"")

        if self.snr_enabled:
            self._update_snr(chunk)

        commit_decision = self._maybe_commit("chunk", elapsed_ms)
        if commit_decision.action:
            return commit_decision

        if elapsed_ms >= self.max_duration_ms:
            if self._can_commit():
                return self._finish("timeout_commit", elapsed_ms)
            if not self._max_extension_used and self._should_extend_window():
                self._extend_window()
                return ConfirmDecision(None, None)
            return self._finish("timeout", elapsed_ms)

        return ConfirmDecision(None, None)

    def observe_partial(
        self, token_count: Optional[int], confidence: Optional[float], now_ts: float
    ) -> ConfirmDecision:
        if not self.active or token_count is None:
            return ConfirmDecision(None, None)

        tokens = int(token_count)
        conf = self._coerce_confidence(confidence)
        elapsed_ms = self._elapsed_ms(now_ts)

        if tokens < self.min_tokens:
            return ConfirmDecision(None, None)

        self.partial_tokens = tokens
        self.partial_confidence = conf
        self.partial_ts = now_ts

        if conf is not None and conf < self.min_confidence:
            return self._finish("partial_low_confidence", elapsed_ms)

        commit_decision = self._maybe_commit("partial", elapsed_ms)
        if commit_decision.action:
            return commit_decision

        return ConfirmDecision(None, None)

    def timeout(self, now_ts: float) -> ConfirmDecision:
        if not self.active:
            return ConfirmDecision(None, None)

        elapsed_ms = self._elapsed_ms(now_ts)
        if elapsed_ms < self.max_duration_ms:
            return ConfirmDecision(None, None)

        if self._can_commit():
            return self._finish("timeout_commit", elapsed_ms)
        return self._finish("timeout", elapsed_ms)

    def cancel(self, reason: str, now_ts: float) -> ConfirmDecision:
        if not self.active:
            return ConfirmDecision(None, None)
        return self._finish(reason or "cancel", self._elapsed_ms(now_ts))

    # ------------------------------- Internals --------------------------------

    def _elapsed_ms(self, now_ts: float) -> float:
        return max(0.0, (now_ts - self.start_ts) * 1000.0)

    def _coerce_confidence(self, value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _check_gap(self, now_ts: float) -> ConfirmDecision:
        if not self.active:
            return ConfirmDecision(None, None)
        if not self.last_chunk_ts:
            return ConfirmDecision(None, None)
        gap_ms = (now_ts - self.last_chunk_ts) * 1000.0
        if self.max_gap_ms > 0.0 and gap_ms > self.max_gap_ms:
            if not self.gap_grace_used:
                elapsed = self._elapsed_ms(now_ts)
                early_window = elapsed <= (self.min_duration_ms + self.max_gap_ms)
                if early_window or self.total_bytes <= 12000:
                    self.gap_grace_used = True
                    return ConfirmDecision(None, None)
            return self._finish("gap", self._elapsed_ms(now_ts), {"gap_ms": gap_ms})
        return ConfirmDecision(None, None)

    def _can_commit(self) -> bool:
        if self.partial_tokens < self.min_tokens:
            return False
        if not self.snr_enabled:
            return True
        if self.snr_db is None:
            return False
        floor = self._snr_floor()
        self._last_snr_floor = floor
        return self.snr_db >= floor

    def _maybe_commit(self, trigger: str, elapsed_ms: float) -> ConfirmDecision:
        if elapsed_ms >= self.min_duration_ms and self._can_commit():
            return self._finish(trigger, elapsed_ms)
        return ConfirmDecision(None, None)

    def _snr_floor(self) -> float:
        if not self.snr_enabled:
            return 0.0
        base = self._base_snr_floor
        if self.snr_db is None:
            return base

        dynamic_relaxation = 0.0
        if self.total_bytes >= 4800:
            dynamic_relaxation += 0.5
        if self.total_bytes >= 9600:
            dynamic_relaxation += 0.5
        if (
            self.partial_confidence is not None
            and self.partial_confidence >= max(self.min_confidence, 0.6)
        ):
            dynamic_relaxation += 0.25

        if dynamic_relaxation >= base:
            return 0.0
        return max(0.0, base - dynamic_relaxation)

    def _should_extend_window(self) -> bool:
        if not self.active:
            return False
        if self.partial_tokens >= self.min_tokens and self.partial_confidence is not None:
            # If ASR has already produced a confident partial, there is no
            # reason to extend unless SNR is borderline.
            floor = self._snr_floor()
            return self.snr_db is not None and self.snr_db + 0.75 >= floor
        # Without a partial transcript yet, give the ASR a chance to catch up
        # provided we have seen meaningful audio.
        return self.total_bytes >= 2400

    def _extend_window(self) -> None:
        self._max_extension_used = True
        self.max_duration_ms += max(300, min(600, self.min_duration_ms))

    def _window_extension_ms(self) -> Optional[int]:
        extension = self.max_duration_ms - self.min_duration_ms - self._initial_window_span
        if extension <= 0:
            return None
        return int(extension)

    def _update_snr(self, chunk: bytes) -> None:
        if not chunk:
            return
        sample_count = len(chunk) // 2
        if sample_count <= 0:
            return
        fmt = f"<{sample_count}h"
        try:
            samples = struct.unpack(fmt, chunk[: sample_count * 2])
        except struct.error:
            return

        total = 0.0
        for s in samples:
            total += float(s) * float(s)
        if sample_count == 0:
            return
        rms = math.sqrt(total / sample_count)
        if rms <= 0.0:
            return

        if self.noise_rms is None:
            self.noise_rms = max(1.0, rms * 0.4)
        else:
            if rms < self.noise_rms:
                alpha = 0.10
                self.noise_rms = (1 - alpha) * self.noise_rms + alpha * rms
            else:
                alpha = 0.02
                self.noise_rms = (1 - alpha) * self.noise_rms + alpha * rms

        self.peak_rms = max(self.peak_rms, rms)
        noise = max(self.noise_rms or 1.0, 1e-3)
        snr = 20.0 * math.log10(max(rms, 1e-3) / noise)
        self.snr_db = snr

    def _finish(
        self, reason: str, elapsed_ms: float, extra: Optional[Dict[str, object]] = None
    ) -> ConfirmDecision:
        self.active = False
        relaxation = None
        if self._last_snr_floor is not None:
            relaxation = max(0.0, round(self._base_snr_floor - self._last_snr_floor, 2))
        metrics: Dict[str, object] = {
            "reason": reason,
            "elapsed_ms": int(elapsed_ms),
            "snr_db": round(self.snr_db, 2) if self.snr_db is not None else None,
            "snr_floor_db": round(self._last_snr_floor, 2)
            if self._last_snr_floor is not None
            else None,
            "snr_relaxation_db": relaxation,
            "gap_grace_used": self.gap_grace_used,
            "noise_rms": round(self.noise_rms, 2) if self.noise_rms is not None else None,
            "peak_rms": round(self.peak_rms, 2) if self.peak_rms else None,
            "partial_tokens": self.partial_tokens or None,
            "partial_confidence": self.partial_confidence,
            "total_bytes": self.total_bytes,
            "window_extended_ms": self._window_extension_ms(),
        }
        if extra:
            metrics.update(extra)
        return ConfirmDecision("commit" if reason in ("chunk", "partial", "timeout_commit") else "abort", metrics)
