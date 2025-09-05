# app/services/providers/mock_provider.py
import uuid
from typing import Dict, Any

class MockProvider:
    def new_turn_id(self) -> str:
        return str(uuid.uuid4())

    def generate_reply(
        self,
        prompt: str,
        persona: Dict[str, Any] | None = None,
        teacher_move: str | None = None,
        context: Dict[str, Any] | None = None,
    ) -> str:
        name = (persona or {}).get("id") or (persona or {}).get("name") or "Chip"
        move = f" (move:{teacher_move})" if teacher_move else ""
        kb   = (context or {}).get("kb") or []
        kb_tag = f" [KB:{len(kb)}]" if kb else ""
        text = (prompt or "").strip() or "Howdy—let's get rolling."
        # Keep it short and conversational per persona rules
        return f"{name}: {text}{move}{kb_tag} (mock)"
