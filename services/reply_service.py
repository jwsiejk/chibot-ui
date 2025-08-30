# services/reply_service.py
"""
Unified reply service with Chip persona + style guard.
- Builds system prompt from prompts/chip_system.md + chip_style.md
- Calls llm_service.chat when available (preferred), falling back to openai_fallback.generate_reply
- Applies style_guard.enforce to keep answers short and flowing (no rigid lists)
"""
from __future__ import annotations
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
        combined = (
            "You are Chip, a Pure Storage virtual systems engineer from Nebraska. "
            "Keep answers short, conversational, and tied to Pure where relevant. "
            "Avoid numbered lists."
        )
    return combined

def _with_ctx_prefix(user_text: str, ctx: Optional[dict]) -> str:
    txt = (user_text or "").strip()
    if not ctx or not isinstance(ctx, dict):
        return txt
    product = (ctx.get("product") or "").strip()
    intent  = (ctx.get("intent") or "").strip()
    hints = []
    if product:
        hints.append(f"Pure topic: {product}")
    if intent:
        hints.append(f"intent: {intent}")
    if hints:
        return "[" + "; ".join(hints) + "] " + txt
    return txt

def _call_llm(user_text: str) -> Tuple[str, Optional[str]]:
    sys_prompt = _system_prompt()
    # Try llm_service.chat with explicit system prompt
    try:
        try:
            txt = llm_service.chat(user_text, system_prompt=sys_prompt)  # type: ignore[arg-type]
            if txt:
                return txt, None
        except TypeError:
            pass
        if hasattr(llm_service, "chat_with_system"):
            txt = llm_service.chat_with_system(system_prompt=sys_prompt, user_text=user_text)  # type: ignore[attr-defined]
            if txt:
                return txt, None
    except Exception as e:
        return "", f"llm_service_error:{type(e).__name__}:{e}"
    return "", "llm_service_empty"

def generate_reply(user_text: str, max_words: int = DEFAULT_MAX_WORDS, ctx: Optional[dict] = None) -> Tuple[str, Optional[str]]:
    user_text = (user_text or "").strip()
    if not user_text:
        return "", "empty_input"

    prompt = _with_ctx_prefix(user_text, ctx)

    # Try LLM path first
    txt, err = _call_llm(prompt)
    if not txt:
        fb, fb_err = openai_fallback.generate_reply("[Follow Chip's persona and style rules]\n" + prompt)
        txt = fb
        err = err or fb_err

    fixed, _issues = enforce(txt, max_words=max_words)
    return fixed, err
