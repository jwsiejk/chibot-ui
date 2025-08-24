# services/llm_service.py
from __future__ import annotations
import os, json, logging
from typing import List, Dict, Any

# ---- Optional OpenAI client ----
_OPENAI_OK = False
try:
    from openai import OpenAI
    _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    _OPENAI_OK = True if os.getenv("OPENAI_API_KEY") else False
except Exception as e:
    _OPENAI_OK = False
    _client = None

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# ---- Chip core style ----
CHIP_SYSTEM = (
    "You are Chip Tracewell: a friendly, plain‑spoken Pure Storage expert from Nebraska.\n"
    "- Sound conversational, not like a manual. Short sentences, natural cadence.\n"
    "- Never use numbered or bulleted lists. No '1.' '2.' or '-' at line starts.\n"
    "- Keep replies tight. Prefer 2–6 short sentences.\n"
    "- Focus on Pure Storage. If the user says something off‑topic, steer back politely.\n"
    "- If the user's intent is ambiguous, ask a quick clarifying question before giving details.\n"
    "- If asked for steps, write them as sentences separated by newlines or commas (no numbers).\n"
    "- End with one short, optional follow‑up (e.g., 'Want me to go deeper on HA or keep it high level?')."
)

def _sanitize_no_lists(text: str) -> str:
    """Flatten simple 1./- lists into natural sentences; also trim heavy formatting."""
    if not text:
        return text
    lines = [l.strip() for l in str(text).splitlines() if l.strip()]
    # If most lines look like bullets, join them.
    bullet_like = sum(1 for l in lines if re_bullet.match(l))
    if bullet_like >= max(2, len(lines)//2):
        parts = [re_bullet.sub("", l).strip(" :-") for l in lines]
        # Turn into spoken flow.
        flow = []
        for i, p in enumerate(parts):
            if i == 0: flow.append(f"First, {p}")
            elif i == 1: flow.append(f"Next, {p}")
            elif i == len(parts)-1: flow.append(f"Finally, {p}")
            else: flow.append(f"Then, {p}")
        return " ".join(flow)
    # Otherwise, remove leading bullet tokens and keep paragraphing.
    cleaned = [re_bullet.sub("", l).strip(" :-") for l in lines]
    return "\n".join(cleaned)

import re
re_bullet = re.compile(r'^\s*(?:\d+[\.\)]|[-*•])\s+')

def _messages(system: str, user: str, history: List[Dict[str, str]]|None=None) -> List[Dict[str,str]]:
    msgs: List[Dict[str,str]] = []
    if system: msgs.append({"role":"system","content":system})
    if history:
        for m in history:
            role = (m.get("role") or "user").lower()
            content = m.get("content") or m.get("message") or m.get("text") or ""
            if content:
                msgs.append({"role": role, "content": str(content)})
    msgs.append({"role":"user","content": user})
    return msgs

def _call_openai(msgs: List[Dict[str,str]], max_tokens: int=300, temperature: float=0.35) -> str:
    if not _OPENAI_OK or _client is None:
        return ""  # caller will handle fallback
    try:
        resp = _client.chat.completions.create(
            model=MODEL,
            messages=msgs,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        text = (resp.choices[0].message.content or "").strip()
        return _sanitize_no_lists(text)
    except Exception as e:
        logging.exception("OpenAI call failed")
        return ""

# ---- Public API ----

def generate_reply(prompt: str|None=None, messages: List[Dict[str,str]]|None=None,
                   history: List[Dict[str,str]]|None=None, context_messages: List[Dict[str,str]]|None=None,
                   **kwargs) -> str:
    """Compatibility wrapper used in a few places."""
    hist = messages or history or context_messages or []
    user = prompt or (hist[-1]["content"] if hist else "")
    msgs = _messages(CHIP_SYSTEM, user, hist[:-1] if hist else None)
    out = _call_openai(msgs, max_tokens=kwargs.get("max_tokens", 300), temperature=kwargs.get("temperature", 0.35))
    if out:
        return out
    return "I'm up, just missing my model key. Tell me the product and your goal, and I’ll help."

def generate_response(user_text: str, history: List[Dict[str,str]]|None=None, **kwargs) -> Dict[str,str]:
    msgs = _messages(CHIP_SYSTEM, user_text, history or [])
    out = _call_openai(msgs, max_tokens=kwargs.get("max_tokens", 320))
    if not out:
        return {"text": "Chip is running (fallback). Tell me your goal and product."}
    return {"text": out}

def generate_greeting(profile: Dict[str,Any]|None=None) -> str:
    name = (profile or {}).get("name") or ""
    region = (profile or {}).get("region") or ""
    who = f"{name} in {region}" if (name and region) else (name or region or "there")
    msgs = _messages(CHIP_SYSTEM, f"Open with a single friendly line: 'Hey—Chip here.' Then ask what to tackle, addressing {who}.")
    out = _call_openai(msgs, max_tokens=40, temperature=0.3)
    return out or "Hey—Chip here. What are we tackling today?"

def phrase_data(role: str, data: Dict[str,Any], history: List[Dict[str,str]]|None=None, **kwargs) -> str:
    prompt = f"Phrase this succinctly for a Pure Storage context (one sentence, no lists). ROLE={role}; DATA={json.dumps(data, ensure_ascii=False)}"
    msgs = _messages(CHIP_SYSTEM, prompt, history or [])
    out = _call_openai(msgs, max_tokens=60, temperature=0.4)
    return out or "I can phrase that once my model key is configured."

def generate_followup(user_text: str, assistant_text: str, history: List[Dict[str,str]]|None=None, **kwargs) -> Dict[str,str]:
    prompt = (
        "Given the last turn, produce one short, natural follow‑up question that continues the same topic. "
        "No bullets, no lists. If a follow‑up wouldn't add value, return an empty line.\n"
        f"USER: {user_text}\nASSISTANT: {assistant_text}"
    )
    msgs = _messages(CHIP_SYSTEM, prompt, history or [])
    out = _call_openai(msgs, max_tokens=40, temperature=0.3)
    return {"text": out.strip()} if out else {"text": ""}

def generate_nudge(state_hint: Dict[str,Any]|None=None, history: List[Dict[str,str]]|None=None, **kwargs) -> Dict[str,str]:
    state_hint = state_hint or {}
    prompt = (
        "Write one gentle nudge to keep momentum (one sentence). "
        "If you can, personalize with product/account from STATE. No lists.\n"
        f"STATE: {json.dumps(state_hint, ensure_ascii=False)}"
    )
    msgs = _messages(CHIP_SYSTEM, prompt, history or [])
    out = _call_openai(msgs, max_tokens=40, temperature=0.3)
    return {"text": out or ""}
