"""Minimal schema validation for chat.v2 frames."""
from __future__ import annotations

from typing import Tuple

_ALLOWED_AUDIO_FORMATS = {"opus", "pcm"}


def _is_int(value: object) -> bool:
    """Return True if value is an int but not a bool."""
    return isinstance(value, int) and not isinstance(value, bool)


def validate_frame(frame: dict) -> Tuple[bool, str | None]:
    """Validate a decoded JSON frame.

    Parameters
    ----------
    frame:
        JSON object parsed from the WebSocket text frame.

    Returns
    -------
    (ok, hint):
        ok is ``True`` when the frame passes validation. hint contains a
        human-readable detail when validation fails.
    """

    if not isinstance(frame, dict):
        return False, "Frame must be a JSON object"

    frame_type = frame.get("type")
    if not isinstance(frame_type, str):
        return False, "Frame requires a string 'type' field"

    if frame_type == "audio.header":
        fmt = frame.get("format")
        if fmt not in _ALLOWED_AUDIO_FORMATS:
            return False, "audio.header requires format 'opus' or 'pcm'"

        sample_rate = frame.get("sample_rate")
        if not _is_int(sample_rate):
            return False, "audio.header requires integer sample_rate"

        channels = frame.get("channels")
        if not _is_int(channels):
            return False, "audio.header requires integer channels"

    return True, None


__all__ = ["validate_frame"]
