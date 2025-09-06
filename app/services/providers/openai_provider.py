# app/services/providers/openai_provider.py
from __future__ import annotations
import uuid
from typing import Any, Dict, Optional, Callable

MakeClient = Callable[[], Any]

class OpenAIProvider:
    """
    Network-agnostic adapter.
    - No SDK imports here.
    - Reads model from cfg (openai_model), not environment.
    - Requires a client factory to be passed in by the loader.
    """
    def __init__(self, cfg: Optional[Dict[str, Any]] = None, *, client_factory: Optional[MakeClient] = None) -> None:
        self.model = (cfg or {}).get("openai_model") or "gpt-4o-mini"
        self._make = client_factory
        self._client = None

    def _client_lazy(self):
        if self._client is None:
            if not self._make:
                raise RuntimeError("OpenAI client not available (no client_factory injected).")
            self._client = self._make()
        return self._client

    def new_turn_id(self) -> str:
        return str(uuid.uuid4())

    def generate_reply(
        self,
        prompt: str,
        persona: Dict[str, Any] | None = None,
        teacher_move: str | None = None,
        context: Dict[str, Any] | None = None,
    ) -> str:
        who = (persona or {}).get("id", "Chip")
        pre = (context or {}).get("preamble") or ""
        kb = (context or {}).get("kb") or []
        kb_hint = f" Use the provided knowledge base items ({len(kb)} items) if relevant." if kb else ""
        system = (f"You are {who}, a concise, friendly Pure Storage virtual systems engineer. "
                  f"Speak conversationally and stay on-brand.{kb_hint}\n{pre}").strip()
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": (prompt or "Hello")},
        ]
        resp = self._client_lazy().chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0.3,
            stream=False,
        )
        return (resp.choices[0].message.content or "").strip()
