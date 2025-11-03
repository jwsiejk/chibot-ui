"""Minimal schema validation for chat.v2 frames."""
from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional, Tuple

from app.logging_setup import current_sid
from app.telemetry import bus

_ALLOWED_AUDIO_FORMATS = {"pcm"}


_logger = logging.getLogger(__name__)


def _emit_validator_log(level: str, msg: str) -> None:
    event = {
        "type": "EVT_LOG",
        "logger": "app.ws.validator",
        "level": level,
        "msg": msg,
    }
    sid = current_sid.get()
    if isinstance(sid, str) and sid:
        event["sid"] = sid
    try:
        bus.publish(event)
    except Exception:  # pragma: no cover - defensive
        _logger.debug("evt=validator_emit_log_failed", exc_info=True)

try:  # pragma: no cover - policy model optional in tests
    from app.policy.model import Policy  # type: ignore
except Exception:  # pragma: no cover - fallback when model absent
    Policy = Any  # type: ignore  # noqa: N816 - mimic type alias


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
            return False, "audio.header requires format 'pcm'"

        sample_rate = frame.get("sample_rate")
        if sample_rate is not None and not _is_int(sample_rate):
            return False, "audio.header requires integer sample_rate"

        channels = frame.get("channels")
        if channels is not None and not _is_int(channels):
            return False, "audio.header requires integer channels"

        if fmt == "pcm":
            expected_sr = 16000
            expected_channels = 1
            got_sr = sample_rate
            got_channels = channels
            if got_sr is None or got_channels is None:
                _logger.warning(
                    "audio.header_rejected reason=pcm_params_mismatch got_sr=%s got_ch=%s",
                    got_sr,
                    got_channels,
                )
                return (
                    False,
                    "audio.header pcm requires sample_rate=16000 and channels=1",
                )
            if got_sr != expected_sr or got_channels != expected_channels:
                _logger.warning(
                    "audio.header_rejected reason=pcm_params_mismatch got_sr=%s got_ch=%s",
                    got_sr,
                    got_channels,
                )
                return (
                    False,
                    "audio.header pcm requires sample_rate=16000 and channels=1",
                )

    return True, None


def validate_audio_header_against_policy(
    header: Dict[str, Any],
    policy: Policy | Mapping[str, Any] | None,
    vendor: Optional[str] = None,
) -> Optional[str]:
    fmt = header.get("format")
    rate = header.get("sample_rate")
    channels = header.get("channels")

    if vendor == "speechmatics":
        if fmt != "pcm" or rate != 16000 or channels != 1:
            _logger.warning(
                "audio.header_rejected reason=vendor_requires_pcm got_format=%s got_sr=%s got_ch=%s",
                fmt,
                rate,
                channels,
            )
            _emit_validator_log(
                "WARNING",
                "audio.header_rejected reason=vendor_requires_pcm vendor=speechmatics",
            )
            return "speechmatics requires pcm@16k mono"

    if policy is None:
        return None

    if isinstance(policy, Mapping):
        media = policy.get("media")
    else:
        media = getattr(policy, "media", None)

    if media is None:
        return None

    if isinstance(media, Mapping):
        get_value = media.get
    else:
        get_value = lambda key, default=None: getattr(media, key, default)

    asr_input = get_value("asr_input")
    if asr_input == "pcm_16k":
        if fmt != "pcm":
            msg = "policy_violation: expected format=pcm"
            _emit_validator_log("WARNING", f"evt=policy_violation detail={msg}")
            return msg
        if rate is not None and rate != 16000:
            msg = f"evt=audio_header_rate_mismatch expected=16000 got={rate}"
            _logger.warning(msg)
            _emit_validator_log("WARNING", msg)
        if channels is not None and channels != 1:
            msg = f"evt=audio_header_channels_mismatch expected=1 got={channels}"
            _logger.warning(msg)
            _emit_validator_log("WARNING", msg)
    else:
        if asr_input:
            detail = f"policy_violation: media.asr_input must be pcm_16k (got {asr_input})"
        else:
            detail = "policy_violation: media.asr_input must be pcm_16k"
        _emit_validator_log("WARNING", f"evt=policy_violation detail={detail}")
        return detail

    return None


__all__ = ["validate_frame", "validate_audio_header_against_policy"]
