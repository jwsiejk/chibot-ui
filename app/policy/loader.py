"""Interaction policy loader.

Provides the effective voice runtime policy while allowing in-process caching
and optional admin overrides sourced from the config store.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Mapping, MutableMapping

from app.db import db

_DEFAULT_POLICY: Dict[str, Any] = {
    "voice_runtime": {
        "confirm_window": {
            "first_turn": {
                "min_ms": 420,
                "max_ms": 1200,
                "until_asr_ready": True,
            },
            "warm_turn": {
                "min_ms": 420,
                "max_ms": 1020,
                "until_asr_ready": False,
            },
        },
        "snr_threshold_db": {
            "first_turn": 8.0,
            "warm_turn": 8.0,
        },
        "barge_in": {
            "allow_ptt": True,
            "allow_local_vad": True,
            "require_asr_evidence": False,
            "suppress_during_tts": "all",
            "post_tts_hold_ms": 200,
        },
        "auto_commit": {
            "enabled": True,
            "requires_dual_evidence": False,
            "asr_ready_required": False,
        },
    }
}

_POLICY_CACHE: Dict[str, Any] | None = None


def _deep_update(target: MutableMapping[str, Any], source: Mapping[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, Mapping) and isinstance(target.get(key), MutableMapping):
            _deep_update(target[key], value)  # type: ignore[index]
        else:
            target[key] = copy.deepcopy(value)


def _load_overrides() -> Mapping[str, Any]:
    try:
        cfg = db.get_config() or {}
    except Exception:
        return {}
    for key in ("interaction_policy_overrides", "interaction_policy"):
        overrides = cfg.get(key)
        if isinstance(overrides, Mapping):
            return overrides
    return {}


def _build_effective_policy() -> Dict[str, Any]:
    policy = copy.deepcopy(_DEFAULT_POLICY)
    overrides = _load_overrides()
    if overrides:
        _deep_update(policy, overrides)
    return policy


def load_policy(*, refresh: bool = False) -> Dict[str, Any]:
    """Return the effective interaction policy.

    The result is cached per-process; callers receive a deep copy to prevent
    accidental mutation of the shared cache.
    """

    global _POLICY_CACHE

    if refresh or _POLICY_CACHE is None:
        _POLICY_CACHE = _build_effective_policy()
    return copy.deepcopy(_POLICY_CACHE)
