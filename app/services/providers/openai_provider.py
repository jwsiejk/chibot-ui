
# app/services/providers/openai_provider.py
import os, json, urllib.request

class OpenAIProvider:
    def __init__(self):
        # In a real implementation we'd check OPENAI_API_KEY and model;
        # here we keep it inert/deterministic (no network).
        self.model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    def new_turn_id(self) -> str:
        return str(uuid.uuid4())
    def generate_reply(self, prompt: str, persona: Dict[str, Any] | None = None,
                       teacher_move: str | None = None, context: Dict[str, Any] | None = None) -> str:
        # Deterministic response for offline tests
        who = (persona or {}).get("id","Chip")
        mv  = f" ({teacher_move})" if teacher_move else ""
        kb = (context or {}).get('kb') or []
pre = (context or {}).get('preamble') or ""
kb_note = f" KB:{len(kb)}" if kb else ""
return f"{who}: {prompt.strip() or 'Hello'} → ok{mv}{kb_note} [openai-stub:{self.model}]"
