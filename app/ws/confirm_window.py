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
        snr_enabled: bool = True,
    ) -> None:
        self.min_duration_ms = max(0, int(min_duration_ms))
        max_dur = int(max_duration_ms)
        if max_dur <= 0:
            max_dur = self.min_duration_ms + 600
        if max_dur < self.min_duration_ms:
            max_dur = self.min_duration_ms
        self.max_duration_ms = max_dur
        self.max_gap_ms = max(0.0, float(max_gap_ms))
        self.min_tokens = max(1, int(min_tokens))
        self.min_confidence = float(min_confidence)
        self.snr_threshold_db = float(snr_threshold_db)
        self.snr_enabled = bool(snr_enabled)

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
            return self._finish("gap", self._elapsed_ms(now_ts), {"gap_ms": gap_ms})
        return ConfirmDecision(None, None)

    def _can_commit(self) -> bool:
        if self.partial_tokens < self.min_tokens:
            return False
        if not self.snr_enabled:
            return True
        if self.snr_db is None:
            return False
        return self.snr_db >= self.snr_threshold_db

    def _maybe_commit(self, trigger: str, elapsed_ms: float) -> ConfirmDecision:
        if elapsed_ms >= self.min_duration_ms and self._can_commit():
            return self._finish(trigger, elapsed_ms)
        return ConfirmDecision(None, None)

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
        metrics: Dict[str, object] = {
            "reason": reason,
            "elapsed_ms": int(elapsed_ms),
            "snr_db": round(self.snr_db, 2) if self.snr_db is not None else None,
            "noise_rms": round(self.noise_rms, 2) if self.noise_rms is not None else None,
            "peak_rms": round(self.peak_rms, 2) if self.peak_rms else None,
            "partial_tokens": self.partial_tokens or None,
            "partial_confidence": self.partial_confidence,
            "total_bytes": self.total_bytes,
        }
        if extra:
            metrics.update(extra)
        return ConfirmDecision("commit" if reason in ("chunk", "partial", "timeout_commit") else "abort", metrics)
