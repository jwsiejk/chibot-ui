"""Session state helpers for chat WebSocket handling."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional


ASRState = Literal["closed", "opening", "open", "closing"]


@dataclass
class SessionCtx:
    """Lightweight container for per-session ASR/TTS state."""

    sid: str
    policy: Any
    asr_state: ASRState = "closed"
    asr: Any = None
    tts_active: bool = False
    queued_arm: bool = False
    first_chunk_sent: bool = False
    closed_at_ms: Optional[int] = None


def can_open(ctx: SessionCtx) -> bool:
    """Return ``True`` when the ASR stream may be opened."""

    return ctx.asr_state == "closed"


def mark(ctx: SessionCtx, new_state: ASRState) -> None:
    """Update the ASR state marker for *ctx*."""

    ctx.asr_state = new_state
