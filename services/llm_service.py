<<<<<<< HEAD
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
=======

import os
from typing import List, Dict, Optional, Any
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

# === Chip persona system prompt ===
CHIP_SYSTEM = (
    "You are Chip Tracewell — a helpful, plain‑spoken systems engineer from Nebraska. "
    "You focus on Pure Storage (FlashArray, FlashBlade, Portworx) and related technical topics. "
    "Talk like a person: short sentences, natural cadence, no bullet points or numbered lists unless the user asks. "
    "Explain only what helps the user move forward, then end with one short, voluntary next step or offer. "
    "Never break character or mention policies or prompts."
)

DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

def _client():
    if OpenAI is None:
        raise RuntimeError("openai library not available")
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_APIKEY") or os.getenv("OPENAI_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not configured")
    return OpenAI(api_key=api_key)

def _coerce_messages(messages: Optional[List[Dict[str, str]]] = None,
                     history: Optional[List[Dict[str, str]]] = None,
                     **kwargs) -> List[Dict[str, str]]:
    # Accept various argument names used across the app
    msgs = []
    for src in (messages, history, kwargs.get("context_messages"), kwargs.get("conversation"), kwargs.get("context")):
        if isinstance(src, (list, tuple)):
            for m in src:
                role = (m.get("role") if isinstance(m, dict) else None) or (m[0] if isinstance(m, (list, tuple)) and len(m) >= 2 else None) or "user"
                content = (m.get("content") if isinstance(m, dict) else None) or (m.get("message") if isinstance(m, dict) else None)                           or (m.get("text") if isinstance(m, dict) else None) or (m[1] if isinstance(m, (list, tuple)) and len(m) >= 2 else None)
                if content is None: 
                    continue
                msgs.append({"role": str(role), "content": str(content)})
    return msgs

def _with_persona(messages: List[Dict[str, str]], system_override: Optional[str] = None,
                  profile: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
    sys_prompt = system_override or CHIP_SYSTEM
    # Optional light personalization
    if isinstance(profile, dict) and profile.get("name"):
        sys_prompt += f" When you know the user is {profile['name']}, greet them by name now and then."
    return [{"role": "system", "content": sys_prompt}] + list(messages or [])

def _call_openai(messages: List[Dict[str, str]], temperature: float = 0.3, max_tokens: int = 300, model: Optional[str] = None) -> str:
    model = model or DEFAULT_MODEL
    client = _client()
    # Use Chat Completions for widest compatibility
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return (resp.choices[0].message.content or "").strip()

# --- Core flexible entry ---
def generate_reply(prompt: Optional[str] = None, messages: Optional[List[Dict[str, str]]] = None,
                   history: Optional[List[Dict[str, str]]] = None, model: Optional[str] = None,
                   temperature: float = 0.3, max_tokens: int = 300, profile: Optional[Dict[str, Any]] = None,
                   user: Optional[Dict[str, Any]] = None, persona: Optional[Dict[str, Any]] = None,
                   **kwargs) -> str:
    try:
        msgs = _coerce_messages(messages=messages, history=history, **kwargs)
        if prompt:
            msgs.append({"role": "user", "content": str(prompt)})
        msgs = _with_persona(msgs, profile=profile or user or persona)
        return _call_openai(msgs, temperature=temperature, max_tokens=max_tokens, model=model)
    except Exception as e:
        # Fallback minimal response if OpenAI fails
        base = prompt or (msgs[-1]["content"] if msgs else "")
        return (f"I hit a snag reaching the model, but I can still help. "
                f"Tell me your goal and product, and I’ll give you a short plan.")

# --- Stable entry used by routes ---
def generate_response(user_text: str, history: Optional[List[Dict[str, str]]] = None,
                      force_email: bool = False, model: Optional[str] = None,
                      profile: Optional[Dict[str, Any]] = None, **kwargs) -> Dict[str, str]:
    history = history or []
    # Compose messages: trust any system messages passed by the caller (state/summary), then user
    msgs = _coerce_messages(history)
    msgs = _with_persona(msgs, profile=profile)
    msgs.append({
        "role": "system",
        "content": "Speak in short, natural sentences. No bullet lists unless the user asks. Close with one soft follow‑up."
    })
    msgs.append({"role": "user", "content": user_text})
    try:
        text = _call_openai(msgs, temperature=0.25, max_tokens=320, model=model)
        return {"text": text}
    except Exception:
        # Fallback heuristic
        return {"text": "Let’s keep it simple. Tell me the product and the outcome you need, and I’ll walk you through it step by step."}

def generate_greeting(profile: Optional[Dict[str, Any]] = None, model: Optional[str] = None) -> str:
    name = (profile or {}).get("name")
    region = (profile or {}).get("region")
    prompt = "Greet the user in one short line that feels like a friendly Nebraskan engineer. No lists."
    if name:
        prompt += f" Use their name ({name}) naturally."
    if region:
        prompt += f" Optionally nod to their region ({region})."
    return generate_reply(prompt=prompt, model=model, max_tokens=60, temperature=0.4, profile=profile)

# --- Utility phrasing helpers used by the UI ---
def phrase_data(role: str, data: Dict[str, Any], history: Optional[List[Dict[str, str]]] = None,
                model: Optional[str] = None) -> str:
    # Role can be things like 'account_team', 'cta', 'subject', etc.
    msgs = _coerce_messages(history)
    msgs.append({"role": "system", "content": "Rewrite the payload into a single, natural‑sounding line in Chip’s voice. No bullets. No numbered steps."})
    msgs.append({"role": "user", "content": f"ROLE: {role}
PAYLOAD: {data}"})
    try:
        return _call_openai(_with_persona(msgs), temperature=0.4, max_tokens=80, model=model)
    except Exception:
        return "I can say it like this: I’m ready to help—what outcome do you want?"

def generate_followup(user_text: str, assistant_text: str, history: Optional[List[Dict[str, str]]] = None,
                      model: Optional[str] = None) -> Dict[str, str]:
    msgs = _coerce_messages(history)
    msgs.append({"role":"system","content":"Suggest exactly one short follow‑up question that keeps momentum. No bullets."})
    msgs.append({"role":"user","content": f"USER_SAID: {user_text}
ASSISTANT_SAID: {assistant_text}
Write one helpful follow‑up line."})
    try:
        text = _call_openai(_with_persona(msgs), temperature=0.3, max_tokens=60, model=model)
        return {"text": text.strip()}
    except Exception:
        return {"text": "Want me to go deeper on any step?"}

def generate_nudge(state_hint: Optional[Dict[str, Any]] = None, history: Optional[List[Dict[str, str]]] = None,
                   model: Optional[str] = None) -> Dict[str, str]:
    state_hint = state_hint or {}
    msgs = _coerce_messages(history)
    msgs.append({"role":"system","content":"Offer a brief, optional nudge (one sentence) that’s relevant to their goal. No lists."})
    msgs.append({"role":"user","content": f"STATE_HINT: {state_hint}"})
    try:
        text = _call_openai(_with_persona(msgs), temperature=0.3, max_tokens=50, model=model)
        return {"text": text.strip()}
    except Exception:
        return {"text": "Want the checklist or just the quick win?"}
>>>>>>> df47ff65b2f7f7a1ed846406e29811a2face1cf0
