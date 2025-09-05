# app/services/providers/openai_provider.py
import os
import uuid
from typing import Dict, Any

class OpenAIProvider:
    """Offline-friendly OpenAI provider stub.
    If OPENAI_API_KEY is set in production, a real implementation could be wired.
    For tests and local runs we keep it deterministic and *do not* perform network calls.
    """
    def __init__(self):
        # Prefer env override but stay inert by default
        self.model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    def new_turn_id(self) -> str:
        return str(uuid.uuid4())

    def generate_reply(
        self,
        prompt: str,
        persona: Dict[str, Any] | None = None,
        teacher_move: str | None = None,
        context: Dict[str, Any] | None = None,
    ) -> str:
        who = (persona or {}).get("id") or (persona or {}).get("name") or "Chip"
        mv  = f" ({teacher_move})" if teacher_move else ""
        kb  = (context or {}).get("kb") or []
        kb_note = f" KB:{len(kb)}" if kb else ""
        text = (prompt or "").strip() or "Hello"
        return f"{who}: {text} → ok{mv}{kb_note} [openai-stub:{self.model}]"
