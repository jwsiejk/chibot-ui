"""Persona loader and system preamble helpers for Chip."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Mapping

_PERSONA_ENV_VAR = "ASKCHIP_PERSONA_PATH"
_DEFAULT_PERSONA_PATH = Path(__file__).resolve().parents[2] / "config" / "personas" / "chip.json"

_persona_cache: Dict[str, Any] | None = None


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


def build_system_preamble(persona: Mapping[str, Any]) -> str:
    """Compose the single-line system preamble for Chip."""

    title = persona.get("public_title") or persona.get("role_title") or "Chip"
    tone = persona.get("tone_preset") or "mentor"
    domain = persona.get("domain_focus") or "Pure Storage solutions"
    return (
        f"You are {title}, the Virtual Partner Technical Manager for Pure Storage partners; "
        f"respond with a {tone} tone, stay focused on {domain}, and teach through conversation."
    )
