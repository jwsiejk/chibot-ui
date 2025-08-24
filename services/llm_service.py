
# services/llm_service.py — unified LLM helpers for Chip
from __future__ import annotations
import os, json, logging, re
from typing import List, Dict, Any, Optional

# ---- Optional OpenAI client ----
_OPENAI_OK = False
try:
    from openai import OpenAI
    _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    _OPENAI_OK = True if os.getenv("OPENAI_API_KEY") else False
except Exception:
    _OPENAI_OK = False
    _client = None

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
INTENT_MODEL = os.getenv("OPENAI_INTENT_MODEL", MODEL)

# ---------- Core helpers ----------
def _call_openai(messages: List[Dict[str,str]], max_tokens: int=300, temperature: float=0.35) -> str:
    if not _OPENAI_OK:
        return ""
    try:
        resp = _client.chat.completions.create(
            model=MODEL,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logging.warning("openai call failed: %s", e)
        return ""

def _messages(system: str, user: str, history: Optional[List[Dict[str,str]]]=None) -> List[Dict[str,str]]:
    msgs: List[Dict[str,str]] = [{"role":"system","content":system}]
    if history:
        for h in history:
            r = h.get("role") or "user"
            c = h.get("message") or h.get("content") or ""
            if not c: continue
            msgs.append({"role": r, "content": c})
    msgs.append({"role":"user","content": user})
    return msgs

# ---------- Persona & style ----------
def _style_rules(word_cap: int, channel: str, tone: str, verbosity: str) -> str:
    ch_rules = {
        "web": "Keep one short paragraph or two short paragraphs; link ideas with transitions.",
        "slack": "One compact paragraph. Avoid heavy formatting.",
        "whatsapp": "One short message. No code blocks.",
        "app": "Speak plainly; natural cadence; crisp sentences.",
    }.get(channel, "Keep one short paragraph.")
    # Verbosity hint
    verb = "concise" if (verbosity or "concise").lower() == "concise" else "detailed"
    return (
        f"Use at most {word_cap} words unless the user explicitly asked for more. "
        "Never use numbered or bulleted lists. "
        f"Adopt a {tone or 'friendly'} tone and keep answers {verb}. " + ch_rules
    )

CHIP_PERSONA = (
    "You are Chip Tracewell: a friendly, plain‑spoken Pure Storage expert from Nebraska. "
    "Be helpful, humble, and precise. Favor short, natural sentences. "
    "Steer gently back to Pure Storage if the user goes off-topic."
)

# ---------- Public APIs ----------
def generate_reply(prompt: Optional[str]=None, messages: Optional[List[Dict[str,str]]]=None,
                   history: Optional[List[Dict[str,str]]]=None, context_messages: Optional[List[Dict[str,str]]]=None,
                   **kwargs) -> str:
    hist = messages or history or context_messages or []
    user = prompt or (hist[-1]["content"] if hist else "")
    msgs = _messages(CHIP_PERSONA, user, hist[:-1] if hist else None)
    out = _call_openai(msgs, max_tokens=kwargs.get("max_tokens", 300), temperature=kwargs.get("temperature", 0.35))
    if out: return out
    return "I'm up, just missing my model key. Tell me the product and your goal, and I’ll help."

def generate_response(user_text: str, history: Optional[List[Dict[str,str]]]=None, **kwargs) -> Dict[str,str]:
    msgs = _messages(CHIP_PERSONA, user_text, history or [])
    out = _call_openai(msgs, max_tokens=kwargs.get("max_tokens", 320))
    if not out:
        return {"text": "Chip is running (fallback). Tell me your goal and product."}
    return {"text": out}

def generate_greeting(profile: Optional[Dict[str,Any]]=None) -> str:
    name = (profile or {}).get("name") or ""
    region = (profile or {}).get("region") or ""
    who = f"{name} in {region}" if (name and region) else (name or region or "there")
    sys = CHIP_PERSONA + " Respond with one line that starts with 'Hey—Chip here.' Then ask what to tackle, addressing " + who + "."
    msgs = _messages(sys, "Greet the user.")
    out = _call_openai(msgs, max_tokens=40, temperature=0.3)
    return out or "Hey—Chip here. What are we tackling today?"

def phrase_data(role: str, data: Any, history: Optional[List[Dict[str,str]]]=None, **kwargs) -> str:
    prompt = f"Phrase this succinctly for {role}: {json.dumps(data, ensure_ascii=False)}"
    msgs = _messages(CHIP_PERSONA, prompt, history or [])
    out = _call_openai(msgs, max_tokens=80, temperature=0.35)
    return out or "Here it is in plain language."

def generate_followup(user_text: str, assistant_text: str, history: Optional[List[Dict[str,str]]]=None, **kwargs) -> Dict[str,str]:
    prompt = (
        "Offer one short, optional follow-up question tailored to the user's last request and your answer. "
        "No lists; one sentence."
    )
    msgs = _messages(CHIP_PERSONA, prompt + f"\nUSER: {user_text}\nYOU: {assistant_text}", history or [])
    out = _call_openai(msgs, max_tokens=40, temperature=0.35)
    return {"text": out or "Want me to go deeper or keep it high level?"}

def generate_nudge(state_hint: Optional[Dict[str,Any]]=None, history: Optional[List[Dict[str,str]]]=None, **kwargs) -> Dict[str,str]:
    prompt = (
        "Write one gentle nudge to keep momentum (one sentence). "
        "If you can, personalize with product/account from STATE. No lists.\n"
        f"STATE: {json.dumps(state_hint or {}, ensure_ascii=False)}"
    )
    msgs = _messages(CHIP_PERSONA, prompt, history or [])
    out = _call_openai(msgs, max_tokens=40, temperature=0.3)
    return {"text": out or ""}

# ---------- Intelligence pack ----------
def _anti_list(text: str) -> str:
    if not text: return ""
    s = text.replace("\r\n","\n")
    # remove bullets/number prefixes at line starts
    s = re.sub(r"(^|\n)\s*([•\-\*]|\d+[\.)])\s*", lambda m: (m.group(1) or ""), s)
    # remove simple '1. ' within sentences
    s = re.sub(r"\b\d+\.\s*", "", s)
    # collapse extra whitespace
    s = re.sub(r"\n{2,}", "\n", s)
    s = re.sub(r"\s{3,}", "  ", s)
    return s.strip()

def _clip_words(text: str, cap: int) -> str:
    if cap <= 0: return text
    words = re.findall(r"\S+", text or "")
    if len(words) <= cap: return text
    return " ".join(words[:cap])

def generate_smart_response(
        user_text: str,
        history: Optional[List[Dict[str,str]]]=None,
        intent: Optional[Dict[str,Any]]=None,
        session_summary: Optional[str]=None,
        memories: Optional[List[str]]=None,
        prefs: Optional[Dict[str,Any]]=None,
        channel: str="web",
        word_cap: int=30,
        temperature: float=0.35,
    ) -> Dict[str,str]:
    """Compose a context-rich prompt with persona, memory, and routing hints."""
    history = history or []
    prefs = prefs or {"tone":"friendly","verbosity":"concise","channel":channel}
    channel = (channel or prefs.get("channel") or "web").lower()
    style = _style_rules(word_cap, channel, prefs.get("tone","friendly"), prefs.get("verbosity","concise"))

    intent_info = intent or {"intent":"unknown","entities":{},"confidence":0.0}
    task = intent_info.get("intent","unknown")
    ents = intent_info.get("entities",{})
    steer_map = {
        "how_to": "Give step‑by‑step as short sentences separated by commas or newlines (no numbers).",
        "troubleshoot": "State likely causes then concise validation steps.",
        "compare": "Compare 2–3 crisp differences and when to choose each.",
        "design": "Give key design considerations and one configuration tip.",
        "upgrade": "Outline preparation, key steps, and rollback note.",
        "info": "Give a compact overview with 1–2 technical specifics.",
        "unknown": "Ask one precise clarifying question before answering.",
    }
    steer = steer_map.get(task, steer_map["unknown"])

    sys = CHIP_PERSONA + "\n" + style + "\nTask steering: " + steer
    ctx_parts = []
    if session_summary: ctx_parts.append("Session summary: " + session_summary)
    if memories: ctx_parts.append("Long‑term notes: " + " | ".join(memories[:3]))
    if ents: ctx_parts.append("Entities: " + json.dumps(ents, ensure_ascii=False))

    user_payload = (
        f"Context: {' | '.join(ctx_parts) if ctx_parts else 'n/a'}\n"
        f"User: {user_text}"
    )

    msgs = _messages(sys, user_payload, history)
    out = _call_openai(msgs, max_tokens=360, temperature=temperature)
    if not out:
        out = "I can help. Do you want an overview, how‑to, troubleshooting, or a comparison?"

    out = _anti_list(out)
    out = _clip_words(out, word_cap)
    return {"text": out, "intent": intent_info}

def summarize_session(messages: List[Dict[str,str]]) -> str:
    """Return a 120–150 word running summary suitable for context, or '' if unavailable."""
    if not _OPENAI_OK or not messages:
        return ""
    sys = (
        "Summarize the conversation so far in 120–150 words. "
        "Capture user goals, products, prior answers, and open items. "
        "No bullets; natural sentences."
    )
    joined = []
    for m in messages[-12:]:  # last few only
        role = m.get("role","user")
        content = m.get("message") or m.get("content") or ""
        if not content: continue
        joined.append(f"{role.upper()}: {content}")
    text = "\n".join(joined)
    msgs = _messages(sys, text, None)
    out = _call_openai(msgs, max_tokens=180, temperature=0.2)
    return out or ""
