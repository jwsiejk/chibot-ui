"""Helpers for rendering policy dataclasses into runtime snapshots."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Dict, Mapping

from app.policy.model import CapturePolicy, MediaPolicy


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
            if name in {"media", "capture"}:
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


def policy_to_snapshot(policy: Any) -> Dict[str, Any]:
    """Render a policy object into the runtime snapshot structure."""

    snapshot = existing_policy_to_snapshot(policy)

    media_policy = _ensure_media(policy)
    capture_policy = _ensure_capture(policy)

    snapshot["media"] = asdict(media_policy)
    snapshot["capture"] = asdict(capture_policy)

    return snapshot


__all__ = ["policy_to_snapshot", "existing_policy_to_snapshot"]
