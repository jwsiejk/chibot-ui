"""Dataclasses describing the runtime policy configuration."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional


@dataclass
class MediaPolicy:
    """Describe how audio should be transported to the ASR provider."""

    asr_input: str = "webm_opus"
    asr_rate_hz: int = 48000
    asr_channels: int = 1
    fallbacks_allowed: bool = False


@dataclass
class CapturePolicy:
    """Describe how microphone capture should behave client-side."""

    start_on_asr_ready: bool = True
    start_on_turn_ready: bool = True
    timeslice_ms: int = 200
    mask_during_tts: bool = True
    mask_keepalive_enable: bool = True
    mask_keepalive_ms: int = 5000


@dataclass
class ASRVendorPolicy:
    """Describe how ASR vendors should be selected at runtime."""

    primary: str = "deepgram"
    secondary: Optional[str] = None


@dataclass
class ASRPolicy:
    """Server-side ASR endpointing configuration."""

    vendor: ASRVendorPolicy = field(default_factory=ASRVendorPolicy)
    prearm_on_tts_end: bool = True
    keep_stream_warm_ms: int = 30000
    commit_on_vad_silence: bool = True
    commit_silence_ms: int = 900
    max_utterance_ms: int = 8000


@dataclass
class AudioPipelinePolicy:
    """Describe the media pipeline presented to clients."""

    mode: Literal["opus-webm", "pcm16"] = "opus-webm"


@dataclass
class AudioPolicy:
    """Audio-focused policy controls."""

    pipeline: AudioPipelinePolicy = field(default_factory=AudioPipelinePolicy)


@dataclass
class Policy:
    """Container for policy knobs exposed to the runtime."""

    interaction: Dict[str, Any] = field(default_factory=dict)
    media: MediaPolicy = field(default_factory=MediaPolicy)
    capture: CapturePolicy = field(default_factory=CapturePolicy)
    asr: ASRPolicy = field(default_factory=ASRPolicy)
    audio: AudioPolicy = field(default_factory=AudioPolicy)


__all__ = [
    "MediaPolicy",
    "CapturePolicy",
    "ASRPolicy",
    "ASRVendorPolicy",
    "AudioPolicy",
    "AudioPipelinePolicy",
    "Policy",
]
