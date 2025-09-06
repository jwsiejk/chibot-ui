# app/services/providers_real/openai_http_provider.py
from __future__ import annotations
import os, uuid
from typing import Any, Dict, Optional

class OpenAIHTTPProvider:
    """
    Real OpenAI provider (HTTP via official SDK).
    No mocks or local stubs in runtime. Tests can inject a fake 'client'.
    Exposes interface expected by the app:
      - new_turn_id() -> str
      - generate_reply(prompt, persona=None, teacher_move=None, context=None) -> str
    """
    def __init__(self, *, client: Any | None = None, model: Optional[str] = None) -> None:
        self.model = model or os.getenv("OPENAI_MODEL") or "gpt-4o-mini"
        self._client = client or self._make_client()

    def new_turn_id(self) -> str:
        return str(uuid.uuid4())

    def generate_reply(
        self,
        prompt: str,
        persona: Dict[str, Any] | None = None,
        teacher_move: str | None = None,
        context: Dict[str, Any] | None = None,
    ) -> str:
        system = self._build_system_prompt(persona, context)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": (prompt or "Hello")},
        ]
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0.3,
            stream=False,
        )
        return (resp.choices[0].message.content or "").strip()

    # --- internals ---
    def _make_client(self):
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY is not set — production requires real vendor keys.")
        try:
            from openai import OpenAI  # type: ignore
        except Exception as e:
            raise RuntimeError("openai Python SDK is not installed. Add 'openai>=1.0.0' to requirements.txt") from e
        return OpenAI(api_key=key)

    def _build_system_prompt(self, persona: Dict[str, Any] | None, context: Dict[str, Any] | None) -> str:
        who = (persona or {}).get("id", "Chip")
        pre = (context or {}).get("preamble") or ""
        kb = (context or {}).get("kb") or []
        kb_hint = f" Use the provided knowledge base items ({len(kb)} items) if relevant." if kb else ""
        return (
            f"You are {who}, a concise, friendly Pure Storage virtual systems engineer. "
            f"Speak conversationally and stay on-brand.{kb_hint}\n{pre}".strip()
        )
