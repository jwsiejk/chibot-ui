"""Utilities for loading the interaction policy snapshot."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Mapping, Sequence

from app import config

InteractionPolicySnapshot = Dict[str, Any]

_ASSISTANT_TURN_SEQUENCE_KEY = "assistant_turn_sequence"
_DEFAULT_ASSISTANT_TURN_ACTIONS: Sequence[str] = (
    "assistant.say",
    "assistant.await_user",
)

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
    "actions": {
        "allowed": ["answer"],
        "surface_via_suggestions": True,
        _ASSISTANT_TURN_SEQUENCE_KEY: list(_DEFAULT_ASSISTANT_TURN_ACTIONS),
    },
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

    base_overrides = getattr(config, "POLICY_OVERRIDES", None)
    if isinstance(base_overrides, Mapping):
        for key, value in base_overrides.items():
            policy[key] = deepcopy(value)

    if overrides:
        for key, value in overrides.items():
            policy[key] = value

    return policy


def assistant_turn_actions(
    snapshot: Mapping[str, Any] | None = None,
) -> list[str]:
    """Return the configured assistant turn action sequence."""

    actions_block: Mapping[str, Any] | None = None
    if isinstance(snapshot, Mapping):
        actions_candidate = snapshot.get("actions")
        if isinstance(actions_candidate, Mapping):
            actions_block = actions_candidate

    if actions_block is not None:
        candidate_sequence = actions_block.get(_ASSISTANT_TURN_SEQUENCE_KEY)
        if isinstance(candidate_sequence, Sequence) and not isinstance(
            candidate_sequence, (str, bytes)
        ):
            normalized = [
                action.strip()
                for action in candidate_sequence
                if isinstance(action, str) and action.strip()
            ]
            if normalized:
                return normalized

    if snapshot is None:
        return list(_DEFAULT_ASSISTANT_TURN_ACTIONS)

    return assistant_turn_actions(None)


__all__ = [
    "load_interaction_policy",
    "InteractionPolicySnapshot",
    "assistant_turn_actions",
]
