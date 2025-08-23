import os
from typing import List, Dict, Optional
from openai import OpenAI

# --- Chip persona ---
CHIP_SYSTEM = (
    "You are Chip Tracewell, a well-mannered, unintentionally funny, tech-savvy Nebraskan who works as a virtual systems engineer. "
    "Teach clearly and practically with concrete steps. Be concise, personable, and lightly witty—no hype. "
    "Use plain language and, when relevant, accurate Pure Storage details (FlashArray, FlashBlade, Portworx). "
    "Occasionally a gentle Nebraska-ism is fine, but sparingly. "
    "When appropriate, end with a short invitational question to keep the conversation going. "
    "Never mention that you are an AI. Never break character."
)

def _with_persona(messages: List[Dict[str, str]], system_override: Optional[str] = None) -> List[Dict[str, str]]:
    msgs = list(messages or [])
    sys = system_override or CHIP_SYSTEM
    if not msgs or msgs[0].get("role") != "system":
        msgs = [{"role": "system", "content": sys}] + msgs
    return msgs

def _client():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")
    return OpenAI(api_key=api_key)

def generate_reply(messages: Optional[List[Dict[str, str]]] = None, prompt: Optional[str] = None,
                   model: Optional[str] = None, max_tokens: int = 500, temperature: float = 0.7) -> str:
    client = _client()
    model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    if prompt and not messages:
        messages = [{"role": "user", "content": prompt}]
    messages = _with_persona(messages or [])
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return (resp.choices[0].message.content or "").strip()

def generate_greeting(profile: Optional[Dict[str, str]] = None,
                      model: Optional[str] = None, temperature: float = 0.8) -> str:
    """Generate a short, dynamic Chip greeting that subtly uses profile fields.
    Rules: 1–2 sentences; natural spoken phrasing; never list profile fields; end with a friendly question.
    """
    client = _client()
    model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    profile = profile or {}
    name = (profile.get("name") or "").strip()
    title = (profile.get("title") or "").strip()
    region = (profile.get("region") or "").strip()

    # Build a natural hint for Chip, not a strict template.
    hint_parts = []
    if name: hint_parts.append(f"their name is {name}")
    if title: hint_parts.append(f"they work as {title}")
    if region: hint_parts.append(f"they're in {region}")
    hint = ("; ".join(hint_parts)) if hint_parts else "we don't know much about them yet"

    system = CHIP_SYSTEM + " Keep greetings varied—no stock phrases."
    user = (
        "Create a dynamic, warm greeting to start a brief voice chat. "
        "Aim for 1–2 sentences max, natural spoken flow. "
        "Subtly nod to what you know about them and pivot into a friendly, specific question. "
        f"For context: {hint}. "
        "Do NOT say 'your profile says' or list fields. "
        "No emojis."
    )

    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]

    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=120,
    )
    return (resp.choices[0].message.content or "").strip()


# --- Stable entrypoint used by routes (no triage/menu) ---
def generate_response(user_text: str, history=None, force_email: bool=False, model: str=None):
    """Stable entrypoint used by routes. Keeps email drafting opt-in only."""
    history = history or []
    t = (user_text or "").lower()

    # If explicitly asked to email and we have an email module, return a structured hint
    if force_email:
        return {"text": "Email drafting requires a recipient and bullet points. Tell me who to email and the key points."}

    # Try to use an existing chat function if present (compat with older routes)
    try:
        return {"text": chat(user_text, history=history, model=model)}  # type: ignore[name-defined]
    except Exception:
        pass
    try:
        return {"text": generate_chat_completion(prompt=user_text, messages=[{'role':'user','content':user_text}], model=model)}  # type: ignore[name-defined]
    except Exception:
        pass

    # Gentle, non-menu topical nudges (still not required)
    if "flashblade" in t or "flash blade" in t:
        return {"text": "FlashBlade//S: fast file & object for high-concurrency analytics and rapid restore. Tell me your goal and I’ll dive in."}
    if "flasharray" in t or "flash array" in t:
        return {"text": "FlashArray: unified block/file/object with consistently low latency and data reduction. What are you trying to accomplish?"}

    # Neutral default—no options list
    return {"text": "Tell me what you’re trying to do and which product you’re on (FlashArray, FlashBlade, or Portworx). I’ll jump in."}


# --- BEGIN assistant patch: loop-guard + humanizer (no menu language) ---
import re as _re

def _chip_humanize(text: str) -> str:
    if not isinstance(text, str):
        text = str(text or "")
    t = text.strip()
    t = _re.sub(r'^\s*[\-\•\*]\s+', '', t, flags=_re.M)
    t = _re.sub(r'^\s*\d+[\.\)]\s+', '', t, flags=_re.M)
    t = _re.sub(r'\n{2,}', '\n', t).strip()
    t = ' '.join(line.strip() for line in t.splitlines() if line.strip())
    parts = _re.split(r'(?<=[.!?])\s+', t)
    t = ' '.join(parts[:3]).strip()
    if not t:
        t = "Got it. Tell me the product and your goal, and I’ll jump in."
    if not t.endswith("?"):
        t += " Want me to take that a level deeper?"
    return t

def _hist_texts(hist):
    out = []
    for item in hist or []:
        if isinstance(item, dict):
            out.append(str(item.get("message") or item.get("text") or item.get("content") or ""))
        elif isinstance(item, (list, tuple)) and item:
            out.append(str(item[-1]))
        elif isinstance(item, str):
            out.append(item)
    return [s.strip().lower() for s in out if s]

def _detect_product(t: str):
    tl = t.lower()
    if any(k in tl for k in ("flashblade", "flash blade", "flashblase", "flash balde", "fb")): return "flashblade"
    if "flash pla" in tl or "flash player" in tl: return "flashblade"
    if any(k in tl for k in ("flasharray", "flash array", "fa")): return "flasharray"
    if "portworx" in tl: return "portworx"
    return ""

def _detect_task(t: str):
    tl = t.lower()
    if any(k in tl for k in ("brief", "overview", "quick briefing", "summary")): return "briefing"
    if any(k in tl for k in ("troubleshoot", "troubleshooting", "issue", "error", "down", "fail", "broken")): return "troubleshooting"
    if "design" in tl or "architecture" in tl: return "design"
    if "size" in tl or "sizing" in tl or "capacity" in tl: return "sizing"
    return ""

def _infer_from
