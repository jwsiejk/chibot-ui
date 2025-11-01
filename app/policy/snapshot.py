"""Helpers for rendering policy dataclasses into runtime snapshots."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Dict, Mapping

from app.policy.model import (
    ASRPolicy,
    AudioPolicy,
    CapturePolicy,
    MediaPolicy,
)


def _as_mapping(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def existing_policy_to_snapshot(policy: Any) -> Dict[str, Any]:
    """Return the snapshot produced by legacy policy structures."""

    if policy is None:
        return {}

    if isinstance(policy, Mapping):
        return dict(policy)

    interaction = getattr(policy, "interaction", None)
    if isinstance(interaction, Mapping):
        return dict(interaction)

    snapshot_factory = getattr(policy, "to_snapshot", None)
    if callable(snapshot_factory):
        try:
            candidate = snapshot_factory()
        except Exception:  # pragma: no cover - defensive
            candidate = None
        if isinstance(candidate, Mapping):
            return dict(candidate)

    if is_dataclass(policy):
        result = {}
        for field_info in getattr(policy, "__dataclass_fields__", {}).values():
            name = field_info.name
            if name in {"media", "capture", "asr", "audio"}:
                continue
            result[name] = getattr(policy, name)
        return _as_mapping(result)

    return {}


def _ensure_media(policy: Any) -> MediaPolicy:
    media = getattr(policy, "media", None)
    if isinstance(media, MediaPolicy):
        return media
    if is_dataclass(media):
        return MediaPolicy(**asdict(media))  # type: ignore[arg-type]
    if isinstance(media, Mapping):
        return MediaPolicy(**media)  # type: ignore[arg-type]
    return MediaPolicy()


def _ensure_capture(policy: Any) -> CapturePolicy:
    capture = getattr(policy, "capture", None)
    if isinstance(capture, CapturePolicy):
        return capture
    if is_dataclass(capture):
        return CapturePolicy(**asdict(capture))  # type: ignore[arg-type]
    if isinstance(capture, Mapping):
        return CapturePolicy(**capture)  # type: ignore[arg-type]
    return CapturePolicy()


def _ensure_asr(policy: Any) -> ASRPolicy:
    asr = getattr(policy, "asr", None)
    if isinstance(asr, ASRPolicy):
        return asr
    if is_dataclass(asr):
        return ASRPolicy(**asdict(asr))  # type: ignore[arg-type]
    if isinstance(asr, Mapping):
        return ASRPolicy(**asr)  # type: ignore[arg-type]
    return ASRPolicy()


def _ensure_audio(policy: Any) -> AudioPolicy:
    audio = getattr(policy, "audio", None)
    if isinstance(audio, AudioPolicy):
        return audio
    if is_dataclass(audio):
        return AudioPolicy(**asdict(audio))  # type: ignore[arg-type]
    if isinstance(audio, Mapping):
        return AudioPolicy(**audio)  # type: ignore[arg-type]
    return AudioPolicy()


def policy_to_snapshot(policy: Any) -> Dict[str, Any]:
    """Render a policy object into the runtime snapshot structure."""

    snapshot = existing_policy_to_snapshot(policy)

    media_policy = _ensure_media(policy)
    capture_policy = _ensure_capture(policy)
    asr_policy = _ensure_asr(policy)
    audio_policy = _ensure_audio(policy)

    snapshot["media"] = asdict(media_policy)
    snapshot["capture"] = asdict(capture_policy)
    snapshot["policy"] = dict(snapshot.get("policy", {}))
    snapshot["policy"]["asr"] = asdict(asr_policy)
    snapshot["audio"] = asdict(audio_policy)

    return snapshot


__all__ = ["policy_to_snapshot", "existing_policy_to_snapshot"]
