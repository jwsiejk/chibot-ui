def upsert_persona_by_slug(self, slug: str, name: str, config: dict, *, intensity: float = 0.13, is_active: bool = False) -> None: ...

def fetch_intent_patterns(self, persona_id: str) -> list[tuple[str, str, float]]:
    """Return [(intent_name, pattern, weight), ...] ordered by weight desc."""

def fetch_intent_config(self, persona_id: str, intent: str) -> dict:
    """Return {teacher_move, chips, tool, tool_payload, priority} or {}."""
