
# app/services/providers/openai_provider.py
from __future__ import annotations
import uuid
from typing import Any, Dict, Optional, Callable

from ..nlg.humanize import humanize_text

MakeClient = Callable[[], Any]

def _limit_sentences(text: str, max_s: int) -> str:
    """Best-effort cap on number of sentences without breaking formatting."""
    try:
        import re
        parts = re.split(r'(?<=[.!?])\s+', (text or '').strip())
        if max_s and max_s > 0 and len(parts) > max_s:
            parts = parts[:max_s]
        return ' '.join(p.strip() for p in parts if p.strip())
    except Exception:
        return text or ''

class OpenAIProvider:
    """
    Network-agnostic adapter.
    - No SDK imports here.
    - Reads model from cfg (openai_model), not environment.
    - Requires a client factory to be passed in by the loader.
    """
    def __init__(self, cfg: Optional[Dict[str, Any]] = None, *, client_factory: Optional[MakeClient] = None) -> None:
        self.cfg = cfg or {}
        if client_factory is None:
            raise RuntimeError("OpenAIProvider requires a client_factory")
        self._client_factory = client_factory
        self._client = None

    @property
    def model(self) -> str:
        # default sane model if not configured explicitly
        return (self.cfg.get("openai_model") or self.cfg.get("OPENAI_MODEL") or "gpt-4o-mini")

    def _client_lazy(self):
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    def new_turn_id(self) -> str:
        return str(uuid.uuid4())

    def generate_reply(self, prompt: str, persona: Dict[str, Any] | None = None,
                       teacher_move: str | None = None, context: Dict[str, Any] | None = None) -> str:
        who = (persona or {}).get("id") or "Chip"
        pre = (context or {}).get("preamble") or ""
        kb = (context or {}).get("kb") or []
        kb_hint = f" Use the provided knowledge base items ({len(kb)} items) if relevant." if kb else ""

        # Extra guidance from config (NLG knobs)
        cfg = self.cfg or {}
        verb = (cfg.get('gen_target_verbosity') or 'medium')
        humor = float(cfg.get('gen_humor', 0.0))
        base_max_s = int(cfg.get('gen_max_sentences', 4))
        max_s = base_max_s
        ctx = context or {}
        move = (teacher_move or "").strip().lower()
        ctx_band = str(ctx.get('confidence_band') or "").strip().lower() if ctx else ""
        ctx_variant = str(ctx.get('clarify_variant') or "").strip().lower() if ctx else ""
        ctx_style = str(ctx.get('clarifier_style') or "").strip().lower() if ctx else ""
        dialog_meta = ctx.get('dialog_meta') if isinstance(ctx, dict) else None
        if dialog_meta and isinstance(dialog_meta, dict):
            if not ctx_band:
                ctx_band = str(dialog_meta.get('confidence_band') or "").strip().lower()
            if not ctx_variant:
                ctx_variant = str(dialog_meta.get('clarify_variant') or "").strip().lower()
            if not ctx_style:
                ctx_style = str(dialog_meta.get('clarifier_style') or "").strip().lower()
        clarifier_high_level = (
            move == "clarify"
            and (
                ctx_style == "clarifier_high_level"
                or ctx_variant == "high_level"
                or ctx_band == "low"
            )
        )
        if clarifier_high_level:
            max_s = 2 if base_max_s <= 0 else min(base_max_s, 2)
            if max_s <= 0:
                max_s = 2
        extra = f" Target verbosity: {verb}. Humor level: {humor:.2f} (0..1). Aim for no more than {max_s} sentences unless necessary."

        system = (f"You are {who}, a concise, friendly Pure Storage virtual systems engineer. "
                  f"Speak conversationally and stay on-brand.{kb_hint}{extra}\n{pre}").strip()

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": (prompt or "Hello")},
        ]

        temperature = float(cfg.get('gen_temperature', 0.3))
        top_p = float(cfg.get('gen_top_p', 1.0))

        resp = self._client_lazy().chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            top_p=top_p,
            stream=False,
        )
        out = (resp.choices[0].message.content or "").strip()
        kb = (context or {}).get('kb') or []
        if isinstance(kb, list):
            out = f"{out} [KB:{len(kb)}]"
        if clarifier_high_level:
            out = humanize_text(out)
        out = _limit_sentences(out, max_s)
        if clarifier_high_level:
            out = humanize_text(out)
        return out
