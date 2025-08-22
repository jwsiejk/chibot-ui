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


# --- Added wrapper to provide a stable entrypoint ---
def generate_response(user_text: str, history=None, force_email: bool=False, model: str=None):
    """Stable entrypoint used by routes. Keeps email drafting opt-in only."""
    history = history or []
    t = (user_text or "").lower()
    # If explicitly asked to email and we have an email module, return a structured hint
    if force_email:
        return {"text": "Email drafting requires a recipient and bullet points. Tell me who to email and the key points."}
    # Try to use an existing chat function if present
    try:
        return {"text": chat(user_text, history=history, model=model)}
    except Exception:
        pass
    try:
        return {"text": generate_chat_completion(prompt=user_text, messages=[{'role':'user','content':user_text}], model=model)}
    except Exception:
        pass
    # Fallback topical replies
    if "flashblade" in t or "flash blade" in t:
        return {"text": "FlashBlade//S: fast file & object for high-concurrency analytics and backup. Want design or sizing help?"}
    if "flasharray" in t or "flash array" in t:
        return {"text": "FlashArray: unified block/file/object with always-on data reduction. Want me to cover replication or NVMe/TCP?"}
    return {"text": "What do you need help with—design, sizing, troubleshooting, or a quick briefing?"}
