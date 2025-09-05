
# app/services/providers/mock_provider.py
import uuid
from typing import Dict, Any

class MockProvider:
    def new_turn_id(self) -> str:
        return str(uuid.uuid4())

    def generate_reply(self, prompt: str, persona: Dict[str, Any] | None = None,
                       teacher_move: str | None = None, context: Dict[str, Any] | None = None) -> str:
        persona_tag = ""
        if persona and isinstance(persona, dict):
            name = persona.get("id") or persona.get("name") or "Chip"
            persona_tag = f"[{name}] "
        move = f"(move:{teacher_move}) " if teacher_move else ""
        # Keep it short and conversational per persona rules
        return f"{persona_tag}Howdy—Chip here. {move}Let's get rolling. (mock)"
