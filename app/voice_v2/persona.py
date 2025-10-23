"""Persona loader and system preamble helpers for Chip."""
from __future__ import annotations

import json
import os
import random
from pathlib import Path
from typing import Any, Dict, Mapping, MutableMapping, Sequence

_PERSONA_ENV_VAR = "ASKCHIP_PERSONA_PATH"
_DEFAULT_PERSONA_PATH = Path(__file__).resolve().parents[2] / "config" / "personas" / "chip.json"

_persona_cache: Dict[str, Any] | None = None

# Session-scoped bookkeeping for rare persona quote injections.
_quote_last_turn_by_sid: Dict[str, int] = {}


def _resolve_persona_path() -> Path:
    override = os.environ.get(_PERSONA_ENV_VAR)
    if override:
        return Path(override).expanduser()
    return _DEFAULT_PERSONA_PATH


def load_persona() -> Dict[str, Any]:
    """Load Chip's persona JSON with an mtime-aware cache."""

    global _persona_cache

    persona_path = _resolve_persona_path()
    stat = persona_path.stat()
    mtime = stat.st_mtime

    if (
        _persona_cache is not None
        and _persona_cache.get("path") == persona_path
        and _persona_cache.get("mtime") == mtime
    ):
        cached = _persona_cache.get("persona")
        if isinstance(cached, dict):
            return cached

    with persona_path.open("r", encoding="utf-8") as handle:
        persona = json.load(handle)

    _persona_cache = {"path": persona_path, "mtime": mtime, "persona": persona}
    return persona


def _coerce_mode(mode: object) -> str:
    return mode if isinstance(mode, str) else ""


def _sanitize_quotes(quotes: object) -> Sequence[str]:
    if isinstance(quotes, Sequence) and not isinstance(quotes, (str, bytes)):
        cleaned = [str(item).strip() for item in quotes if isinstance(item, str) and item.strip()]
        return cleaned
    return ()


def _coerce_policy_int(value: object, minimum: int = 0) -> int | None:
    if isinstance(value, bool):  # bool is int subclass, handle explicitly
        return int(value)
    if isinstance(value, (int, float)):
        coerced = int(value)
        return coerced if coerced >= minimum else minimum
    if isinstance(value, str) and value.strip().lstrip("-+").isdigit():
        coerced = int(value)
        return coerced if coerced >= minimum else minimum
    return None


def _coerce_rate_percent(value: object) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, min(float(value), 100.0))
    if isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return 0.0
        return max(0.0, min(parsed, 100.0))
    return 0.0


def select_quote(
    persona: Mapping[str, Any] | MutableMapping[str, Any] | None,
    mode: str,
    last_used_turns_ago: int | None,
    *,
    rng_seed: int,
) -> Dict[str, str] | None:
    """Deterministically pick a persona quote if policy allows it."""

    if not isinstance(persona, Mapping):
        return None

    quotes = _sanitize_quotes(persona.get("quotes"))
    if not quotes:
        return None

    policy_obj = persona.get("quote_policy")
    policy = policy_obj if isinstance(policy_obj, Mapping) else {}

    normalized_mode = _coerce_mode(mode).lower()
    forbid_on_clarify = bool(policy.get("forbid_on_clarify", False))
    if forbid_on_clarify and normalized_mode == "clarify":
        return None

    allowed_modes = policy.get("modes_allowed")
    if isinstance(allowed_modes, Sequence) and not isinstance(allowed_modes, (str, bytes)):
        normalized_allowed = {str(item).lower() for item in allowed_modes if isinstance(item, str) and item}
        if normalized_allowed and normalized_mode not in normalized_allowed:
            return None

    cooldown_turns = _coerce_policy_int(policy.get("cooldown_turns"), minimum=0)
    if (
        cooldown_turns is not None
        and last_used_turns_ago is not None
        and last_used_turns_ago >= 0
        and last_used_turns_ago < cooldown_turns
    ):
        return None

    rate_percent = _coerce_rate_percent(policy.get("rate_percent"))
    if rate_percent <= 0.0:
        return None

    rng = random.Random(rng_seed)
    if rng.random() >= (rate_percent / 100.0):
        return None

    quote_index = rng.randrange(len(quotes))
    quote_text = quotes[quote_index]
    quote_id = f"quote_{quote_index}"
    return {"id": quote_id, "text": quote_text}


def maybe_pick_quote_for_sid(
    persona: Mapping[str, Any] | MutableMapping[str, Any] | None,
    sid: str,
    mode: str,
    turn_no: int,
) -> Dict[str, str] | None:
    """Return a persona quote for the given session when policy allows."""

    if not isinstance(sid, str) or not sid:
        return None

    last_turn = _quote_last_turn_by_sid.get(sid)
    if last_turn is not None and turn_no >= last_turn:
        delta = turn_no - last_turn
    else:
        delta = None

    quote = select_quote(persona, mode, delta, rng_seed=turn_no)
    if quote:
        _quote_last_turn_by_sid[sid] = turn_no
    return quote


def default_chips_for_mode(p: dict, mode: str) -> list[str]:
    try:
        return list((p.get("modes") or {}).get(mode, {}).get("chips") or [])[:3]
    except Exception:
        return []


def build_system_preamble(persona: Mapping[str, Any]) -> str:
    """Compose the single-line system preamble for Chip."""

    title = persona.get("public_title") or persona.get("role_title") or "Chip"
    tone = persona.get("tone_preset") or "mentor"
    domain = persona.get("domain_focus") or "Pure Storage solutions"
    return (
        f"You are {title}, the Virtual Partner Technical Manager for Pure Storage partners; "
        f"respond with a {tone} tone, stay focused on {domain}, and teach through conversation."
    )
