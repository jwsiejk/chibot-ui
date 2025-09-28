from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from app.db import db
from app.db_dal import DAL, DBConfig


def _coerce_row_value(row: Any, key: str, index: int) -> Any:
    """Helper to access sqlite3.Row / tuple values safely."""
    if isinstance(row, dict):
        return row.get(key)
    if hasattr(row, "keys"):
        try:
            return row[key]
        except Exception:
            pass
    try:
        return row[index]
    except Exception:
        return None


def _ensure_list(obj: Any) -> List[str]:
    if isinstance(obj, list):
        return [str(x) for x in obj]
    if isinstance(obj, tuple):
        return [str(x) for x in obj]
    if isinstance(obj, str):
        # Attempt to interpret simple array encodings (postgres text[] / JSON)
        txt = obj.strip()
        if txt.startswith("[") and txt.endswith("]"):
            try:
                data = json.loads(txt)
                if isinstance(data, list):
                    return [str(x) for x in data]
            except Exception:
                return [txt]
        if txt.startswith("{") and txt.endswith("}"):
            inner = txt[1:-1]
            return [part.strip().strip('"') for part in inner.split(",") if part.strip()]
        return [txt]
    return []


def _ensure_dict(obj: Any) -> Dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, str):
        try:
            data = json.loads(obj)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


class PersonaStore:
    """Persistence facade for persona configuration + few-shots."""

    def __init__(self, *, url: Optional[str] = None) -> None:
        default_url = os.environ.get("DATABASE_URL", "sqlite:///ci_phase15.sqlite3")
        cfg = DBConfig(url=url or default_url)
        self._dal = DAL(cfg)

    # --- Persona core -------------------------------------------------

    def get_active_persona(self) -> Optional[Dict[str, Any]]:
        try:
            rows = self._dal.query(
                "SELECT id, name, intensity, config FROM personas WHERE is_active = ? ORDER BY updated_at DESC LIMIT 1",
                (True,),
            )
        except Exception:
            return None
        if not rows:
            return None
        row = rows[0]
        persona_id = _coerce_row_value(row, "id", 0)
        name = _coerce_row_value(row, "name", 1) or "Chip"
        intensity = float(_coerce_row_value(row, "intensity", 2) or 0.13)
        config = _ensure_dict(_coerce_row_value(row, "config", 3))
        return {"id": str(persona_id), "name": name, "intensity": intensity, "config": config}

    # --- Intent registry ----------------------------------------------

    def fetch_intent_patterns(self, persona_id: str) -> List[Tuple[str, str, float]]:
        if not persona_id:
            return []
        try:
            rows = self._dal.query(
                "SELECT intent_name, pattern, weight FROM persona_intent_patterns WHERE persona_id = ? ORDER BY weight DESC",
                (persona_id,),
            )
        except Exception:
            return []
        out: List[Tuple[str, str, float]] = []
        for row in rows:
            name = _coerce_row_value(row, "intent_name", 0)
            pat = _coerce_row_value(row, "pattern", 1)
            weight = float(_coerce_row_value(row, "weight", 2) or 1.0)
            if name and pat:
                out.append((str(name), str(pat), weight))
        return out

    def fetch_intent_config(self, persona_id: str, intent: str) -> Dict[str, Any]:
        if not persona_id or not intent:
            return {}
        try:
            rows = self._dal.query(
                """
                SELECT teacher_move, chips, tool, tool_payload, priority
                  FROM persona_intents
                 WHERE persona_id = ? AND name = ?
                 ORDER BY priority ASC
                 LIMIT 1
                """,
                (persona_id, intent),
            )
        except Exception:
            return {}
        if not rows:
            return {}
        row = rows[0]
        chips = _ensure_list(_coerce_row_value(row, "chips", 1))
        payload = _ensure_dict(_coerce_row_value(row, "tool_payload", 3))
        return {
            "teacher_move": _coerce_row_value(row, "teacher_move", 0),
            "chips": chips,
            "tool": _coerce_row_value(row, "tool", 2),
            "tool_payload": payload,
            "priority": _coerce_row_value(row, "priority", 4),
        }

    # --- Few-shot examples --------------------------------------------

    def _fetch_examples(self, persona_id: str) -> List[Dict[str, Any]]:
        try:
            rows = self._dal.query(
                """
                SELECT intent, user_pattern, assistant_target
                  FROM persona_examples
                 WHERE persona_id = ?
                 ORDER BY id ASC
                """,
                (persona_id,),
            )
        except Exception:
            return []
        examples: List[Dict[str, Any]] = []
        for row in rows:
            intent = _coerce_row_value(row, "intent", 0)
            user_pat = _coerce_row_value(row, "user_pattern", 1) or ""
            assistant = _coerce_row_value(row, "assistant_target", 2) or ""
            if user_pat and assistant:
                examples.append({
                    "intent": str(intent) if intent else "",
                    "pattern": str(user_pat),
                    "assistant": str(assistant),
                })
        return examples

    @lru_cache(maxsize=32)
    def _cached_examples(self, persona_id: str) -> Tuple[Dict[str, Any], ...]:
        items = self._fetch_examples(persona_id)
        return tuple(items)

    def match_examples(self, persona_id: str, user_text: str, limit: int = 4) -> List[Dict[str, str]]:
        if not persona_id:
            return []
        cached = list(self._cached_examples(persona_id))
        if not cached:
            return []
        text = (user_text or "").strip()
        matches: List[Dict[str, str]] = []
        if text:
            for ex in cached:
                pat = ex.get("pattern", "")
                if not pat:
                    continue
                try:
                    if re.search(pat, text, re.IGNORECASE):
                        matches.append({"user": pat, "assistant": ex.get("assistant", "")})
                        continue
                except re.error:
                    pass
                if pat.lower() in text.lower():
                    matches.append({"user": pat, "assistant": ex.get("assistant", "")})
                if len(matches) >= limit:
                    break
        if not matches:
            for ex in cached[:limit]:
                matches.append({"user": ex.get("pattern", ""), "assistant": ex.get("assistant", "")})
        return matches[:limit]


class PersonaManager:
    """Lightweight persona orchestrator."""

    def __init__(self, store: PersonaStore) -> None:
        self._store = store
        self._cached: Optional[Dict[str, Any]] = None

    def get_active(self) -> Dict[str, Any]:
        if self._cached:
            return self._cached
        persona = self._store.get_active_persona()
        if persona:
            self._cached = persona
            return persona
        # Fallback to in-memory default persona
        memory = getattr(db, "memory", {})
        chip = (memory.get("personas", {}) or {}).get("chip") if isinstance(memory, dict) else None
        fallback = {
            "id": "chip",
            "name": (chip or {}).get("name", "Chip"),
            "intensity": (chip or {}).get("intensity", 0.13),
            "config": (chip or {}).get("config") or {},
        }
        self._cached = fallback
        return fallback
