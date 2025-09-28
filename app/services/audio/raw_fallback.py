"""Utilities for detecting when to fall back to raw PCM transport.

This module centralises all heuristics that decide whether an incoming
WebSocket audio stream should continue to be treated as containerised Opus or
switched to Deepgram's raw PCM ingest path. The logic lives here so it can be
shared by the WS handler and tests without sprinkling guard-rail state across
the codebase.

The workflow is:

``StreamStats``
    Mutable per-session counters – frame counts, decode errors, jitter, etc.

``DetectionSignal`` & ``StreamMeta``
    Lightweight dataclasses representing what we know about the stream (codec,
    channel count, sample rate hints).

``should_use_raw_fallback``
    Given the latest detection signal and stats, decide if we should coerce the
    Deepgram connection into raw PCM mode.

``coerce_to_raw_config``
    Produce Deepgram configuration overrides that enable raw PCM ingest.

Helper functions provide deterministic frame sizing (padding, silence
insertion) so the WS layer can keep its buffering logic tidy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional

PCM_BYTES_PER_SAMPLE = 2
DEFAULT_SAMPLE_RATE = 48_000
DEFAULT_CHANNELS = 1
DEFAULT_FRAME_MS = 20

# Frame length ≥ this threshold suggests we're receiving PCM rather than Opus.
RAW_PCM_SUSPECT_BYTES = 320  # 20 ms @ 8 kHz mono 16-bit PCM


@dataclass(frozen=True)
class DetectionSignal:
    """Lightweight representation of the current transport detection."""

    container: Optional[str] = None
    codec: Optional[str] = None
    containerized: bool = False
    source: str = "unknown"


@dataclass
class StreamMeta:
    """Mutable hints about the stream supplied by clients or heuristics."""

    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    encoding: str = "opus"
    frame_ms: int = DEFAULT_FRAME_MS
    bits_per_sample: int = 16

    def apply_config(self, cfg: Mapping[str, object]) -> None:
        """Apply a client Configure payload (best-effort)."""

        if not cfg:
            return
        sr = cfg.get("sample_rate") if isinstance(cfg, Mapping) else None
        if isinstance(sr, int) and sr > 0:
            self.sample_rate = sr

        ch = cfg.get("channels") if isinstance(cfg, Mapping) else None
        if isinstance(ch, int) and ch > 0:
            self.channels = ch

        enc = cfg.get("encoding") if isinstance(cfg, Mapping) else None
        if isinstance(enc, str) and enc:
            self.encoding = enc.lower()

        frame = cfg.get("frame_ms") if isinstance(cfg, Mapping) else None
        if not isinstance(frame, int):
            frame = cfg.get("frame_duration_ms") if isinstance(cfg, Mapping) else None
        if isinstance(frame, int) and frame > 0:
            self.frame_ms = frame

    def assume_raw_pcm(self) -> None:
        """Ensure defaults suitable for PCM if the client did not specify them."""

        if not self.sample_rate or self.sample_rate <= 0:
            self.sample_rate = DEFAULT_SAMPLE_RATE
        if not self.channels or self.channels <= 0:
            self.channels = DEFAULT_CHANNELS
        self.encoding = "linear16"


@dataclass
class FallbackGuardrails:
    """Tunable guard rails controlling when the fallback is allowed."""

    max_consecutive_decode_errors: int = 3
    max_total_decode_errors: int = 6
    lenient_prefix_frames: int = 2
    jitter_slip_limit: int = 4
    raw_pcm_suspect_bytes: int = RAW_PCM_SUSPECT_BYTES
    raw_candidate_confirmations: int = 3
    min_errors_for_suspect: int = 1


@dataclass
class StreamStats:
    """Mutable, per-session counters that inform the fallback decision."""

    meta: StreamMeta = field(default_factory=StreamMeta)
    guardrails: FallbackGuardrails = field(default_factory=FallbackGuardrails)
    detection: Optional[DetectionSignal] = None
    frames_seen: int = 0
    decode_errors: int = 0
    consecutive_decode_errors: int = 0
    jitter_slip_frames: int = 0
    silence_frames: int = 0
    normalized_frames: int = 0
    raw_frame_candidates: int = 0
    suspected_raw: bool = False
    forced_fallback: bool = False
    last_frame_size: Optional[int] = None

    def note_detection(self, detection: DetectionSignal) -> None:
        self.detection = detection

    def note_decode_error(self, *, frame_index: Optional[int] = None) -> None:
        self.decode_errors += 1
        idx = frame_index if frame_index is not None else self.frames_seen
        if idx > self.guardrails.lenient_prefix_frames:
            self.consecutive_decode_errors += 1

    def note_jitter_slip(self) -> None:
        self.jitter_slip_frames += 1

    def note_provider_error(self) -> None:
        """Treat provider-originated errors as a decode issue."""

        self.note_decode_error(frame_index=self.frames_seen + 1)

    def note_frame(
        self,
        frame: bytes,
        *,
        normalized: bool,
        success: bool,
        silence: bool,
    ) -> None:
        self.frames_seen += 1
        self.last_frame_size = len(frame)
        if normalized:
            self.normalized_frames += 1
        if silence:
            self.silence_frames += 1
        if success:
            self.consecutive_decode_errors = 0

    def reset_turn(self) -> None:
        """Reset per-turn counters while keeping the session heuristics."""

        self.frames_seen = 0
        self.decode_errors = 0
        self.consecutive_decode_errors = 0
        self.jitter_slip_frames = 0
        self.silence_frames = 0
        self.normalized_frames = 0
        self.last_frame_size = None
        if not self.forced_fallback:
            self.raw_frame_candidates = 0
            self.suspected_raw = False

    def force_fallback(self) -> None:
        self.forced_fallback = True
        self.suspected_raw = True
        self.meta.assume_raw_pcm()

    @property
    def should_normalize_frames(self) -> bool:
        if self.forced_fallback:
            return True
        return (self.meta.encoding or "").lower() in {"pcm", "linear16"}

    def observe_frame(self, frame: bytes) -> bytes:
        """Observe a newly-arrived frame and return the bytes to forward."""

        data = bytes(frame or b"")
        next_idx = self.frames_seen + 1

        if len(data) >= self.guardrails.raw_pcm_suspect_bytes:
            self.raw_frame_candidates += 1
            if self.raw_frame_candidates >= self.guardrails.raw_candidate_confirmations:
                self.suspected_raw = True
            if self.detection and self.detection.containerized:
                # Containerised detection but receiving raw-sized frames — treat as a
                # decode failure so guardrails can trip.
                self.note_decode_error(frame_index=next_idx)
                silence = is_probable_silence(data)
                self.note_frame(data, normalized=False, success=False, silence=silence)
                return data

        if not data:
            self.note_decode_error(frame_index=next_idx)
            if self.should_normalize_frames:
                fill = silence_frame(self.meta)
                if fill:
                    self.note_jitter_slip()
                silence = True
                self.note_frame(fill, normalized=True, success=True, silence=silence)
                return fill
            silence = True
            self.note_frame(data, normalized=False, success=False, silence=silence)
            return data

        if self.should_normalize_frames:
            normalized = normalize_pcm_frame(data, self.meta)
            changed = normalized != data
            if changed:
                self.note_jitter_slip()
            silence = is_probable_silence(normalized)
            self.note_frame(normalized, normalized=True, success=True, silence=silence)
            return normalized

        silence = is_probable_silence(data)
        self.note_frame(data, normalized=False, success=True, silence=silence)
        return data


def expected_pcm_frame_bytes(meta: Optional[StreamMeta]) -> int:
    meta = meta or StreamMeta()
    sr = meta.sample_rate or DEFAULT_SAMPLE_RATE
    ch = meta.channels or DEFAULT_CHANNELS
    frame_ms = meta.frame_ms or DEFAULT_FRAME_MS
    bits = meta.bits_per_sample or (PCM_BYTES_PER_SAMPLE * 8)
    bytes_per_sample = max(1, bits // 8)
    return int(sr * ch * bytes_per_sample * frame_ms / 1000)


def normalize_pcm_frame(frame: bytes, meta: StreamMeta) -> bytes:
    expected = expected_pcm_frame_bytes(meta)
    if expected <= 0:
        return frame
    if len(frame) == expected:
        return frame
    if len(frame) < expected:
        return frame + b"\x00" * (expected - len(frame))
    return frame[:expected]


def pad_frame_to_expected(frame: bytes, meta: StreamMeta) -> bytes:
    """Public wrapper used by tests – identical to ``normalize_pcm_frame``."""

    return normalize_pcm_frame(frame, meta)


def silence_frame(meta: StreamMeta) -> bytes:
    expected = expected_pcm_frame_bytes(meta)
    if expected <= 0:
        return b""
    return b"\x00" * expected


def is_probable_silence(frame: bytes) -> bool:
    if not frame:
        return True
    if len(frame) <= 2:
        return False
    first = frame[:1]
    return frame.count(first) == len(frame) and first in (b"\x00", b"\xff")


def should_buffer_for_jitter(stats: StreamStats) -> bool:
    return stats.jitter_slip_frames >= stats.guardrails.jitter_slip_limit


def should_use_raw_fallback(
    detection: Optional[DetectionSignal],
    stats: Optional[StreamStats],
) -> bool:
    if stats is None:
        return False
    if stats.forced_fallback:
        return True

    detect = detection or stats.detection

    if detect and not detect.containerized and not stats.suspected_raw:
        return False

    guard = stats.guardrails

    if stats.suspected_raw and stats.decode_errors >= guard.min_errors_for_suspect:
        return True

    if detect and detect.containerized:
        if stats.consecutive_decode_errors >= guard.max_consecutive_decode_errors:
            return True

    if stats.decode_errors >= guard.max_total_decode_errors:
        return True

    if should_buffer_for_jitter(stats):
        return True

    return False


def coerce_to_raw_config(meta: StreamMeta) -> dict:
    meta.assume_raw_pcm()
    sample_rate = meta.sample_rate or DEFAULT_SAMPLE_RATE
    channels = meta.channels or DEFAULT_CHANNELS
    frame_ms = meta.frame_ms or DEFAULT_FRAME_MS

    transport = {
        "protocol": "websocket",
        "container": "raw",
        "codec": "pcm",
        "containerized_opus": False,
        "normalized_pcm": True,
        "sample_rate": sample_rate,
        "channels": channels,
        "frame_ms": frame_ms,
        "bits_per_sample": meta.bits_per_sample,
        "raw_fallback": True,
    }

    return {
        "encoding": "linear16",
        "sample_rate": sample_rate,
        "channels": channels,
        "_transport": transport,
    }


__all__ = [
    "DetectionSignal",
    "StreamMeta",
    "StreamStats",
    "FallbackGuardrails",
    "expected_pcm_frame_bytes",
    "normalize_pcm_frame",
    "pad_frame_to_expected",
    "silence_frame",
    "is_probable_silence",
    "should_buffer_for_jitter",
    "should_use_raw_fallback",
    "coerce_to_raw_config",
]
