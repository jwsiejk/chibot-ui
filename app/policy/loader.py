"""Utilities for loading the interaction policy snapshot."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

InteractionPolicySnapshot = Dict[str, Any]

# NOTE: These defaults represent the deterministic policy snapshot returned by
# ``load_interaction_policy`` when no overrides are provided. The structure is
# intentionally shallow so that overrides replace entire child objects rather
# than performing recursive merges.
_DEFAULT_INTERACTION_POLICY: InteractionPolicySnapshot = {
    "mode": "idle",
    "allow_auto_vad": True,
    "barge_in_enabled": True,
    "auto_commit_when_ready": True,
    "voice": {"voice_id": "alloy-en-US-001", "locale": "en-US"},
    "greet": {"enabled": True, "mode": "persona", "post_hold_ms": 200},
    "suggestions": {"on_connect": True, "count": 3},
    "telemetry": {
        "enabled": True,
        "level": "debug",
        "categories": {
            "ws": True,
            "audio": True,
            "policy": True,
            "tts": True,
            "gate": True,
            "barge": True,
            "asr": True,
            "nlu": True,
            "nlg": True,
            "client_ui": True,
            "provider_debug": True,
        },
        "redaction": {"pii": True, "secrets": True, "text": False},
        "sampling": {"percent": 100},
    },
}


def load_interaction_policy(
    overrides: Dict[str, Any] | None = None,
) -> InteractionPolicySnapshot:
    """Return a deterministic interaction policy snapshot.

    The returned snapshot matches the defaults defined by the system of record
    for the `chat.v2` contract. When ``overrides`` are provided, they are merged
    shallowly on top of the defaults—each top-level key in ``overrides``
    replaces the corresponding value entirely.

    Args:
        overrides: Optional dictionary with top-level keys to replace in the
            default policy snapshot. Nested merges are not performed; providing
            a key overrides the whole value at that key.

    Returns:
        A new dictionary containing the policy snapshot with any specified
        overrides applied. Mutating the returned dictionary will not affect the
        stored defaults.
    """

    policy: InteractionPolicySnapshot = deepcopy(_DEFAULT_INTERACTION_POLICY)

    if overrides:
        for key, value in overrides.items():
            policy[key] = value

    return policy


__all__ = ["load_interaction_policy", "InteractionPolicySnapshot"]
