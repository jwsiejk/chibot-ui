# services/reply_service.py
"""
Unified reply service with Chip persona + style guard.
- Builds system prompt from prompts/chip_system.md + chip_style.md
- Calls llm_service.chat when available (preferred), falling back to openai_fallback.generate_reply
- Applies style_guard.enforce to keep answers short and flowing (no rigid lists)
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import Tuple, Optional

from . import llm_service  # type: ignore
from . import openai_fallback  # type: ignore
from .style_guard import enforce, DEFAULT_MAX_WORDS

_ROOT = Path(__file__).resolve().parents[1]
_PROMPTS = _ROOT / "prompts"

def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8").strip()
    except Exception:
        return ""

def _system_prompt() -> str:
    sys_txt = _read(_PROMPTS / "chip_system.md")
    sty_txt = _read(_PROMPTS / "chip_style.md")
    combined = (sys_txt + "\n\n" + sty_txt).strip()
    if not combined:
        # Fallback minimal system prompt
        combined = (
            "You are Chip, a Pure Storage virtual systems engineer. "
            "Keep answers short, conversational, and tied to Pure Storage where relevant. "
            "Do not use numbered lists; use transitions like First, Next, Then, Finally."
        )
    return combined

def _call_llm(user_text: str) -> Tuple[str, Optional[str]]:
    # Preferred: ask llm_service for a reply, attempting to pass a system prompt.
    sys_prompt = _system_prompt()
    try:
        # Try llm_service.chat with kwargs if supported
        try:
            txt = llm_service.chat(user_text, system_prompt=sys_prompt)  # type: ignore[arg-type]
            if txt:
                return txt, None
        except TypeError:
            # Fallback: try a signature without kwargs
            pass

        # Try a variant function if exposed
        if hasattr(llm_service, "chat_with_system"):
            txt = llm_service.chat_with_system(system_prompt=sys_prompt, user_text=user_text)  # type: ignore[attr-defined]
            if txt:
                return txt, None

        # Final attempt: prepend a control tag to the user text (works with many basic wrappers)
        tagged = f"[SYSTEM]\n{sys_prompt}\n[/SYSTEM]\n{user_text}"
        txt = llm_service.chat(tagged)  # type: ignore[arg-type]
        if txt:
            return txt, None

    except Exception as e:
        return "", f"llm_service_error: {type(e).__name__}: {e}"

    return "", "llm_service_empty"

def generate_reply(user_text: str, max_words: int = DEFAULT_MAX_WORDS, ctx: Optional[dict] = None) -> Tuple[str, Optional[str]]:
    user_text = (user_text or "").strip()
    if not user_text:
        return "", "empty_input"

    # Lightweight context hint for product/intent
    if ctx and isinstance(ctx, dict):
        product = (ctx.get('product') or '').strip()
        intent  = (ctx.get('intent') or '').strip()
    else:
        product = ''
        intent  = ''
    if product:
        user_text = f"(Context: We’re discussing Pure Storage {product}. Keep replies short, conversational, and list‑free.)\n" + user_text

    # Try LLM path
    txt, err = _call_llm(user_text)
    if not txt:
        # Fallback path (openai_fallback itself may call OpenAI or echo mode)
        txt, fb_err = openai_fallback.generate_reply(f"[Follow Chip's persona and style rules]\n{user_text}")
        err = err or fb_err

    # Enforce style regardless of path
    fixed, issues = enforce(txt, max_words=max_words)

    # If we changed anything, append a tiny invisible hint to help debugging in logs (not user-visible)
    # (We won't alter returned text with metadata; logging handled by routes.)
    return fixed, err
