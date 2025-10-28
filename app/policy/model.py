"""Dataclasses describing the runtime policy configuration."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


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


@dataclass
class Policy:
    """Container for policy knobs exposed to the runtime."""

    interaction: Dict[str, Any] = field(default_factory=dict)
    media: MediaPolicy = field(default_factory=MediaPolicy)
    capture: CapturePolicy = field(default_factory=CapturePolicy)


__all__ = ["MediaPolicy", "CapturePolicy", "Policy"]
