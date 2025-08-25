# services/llm_service.py
import os
from openai import OpenAI

_client = None

def _client_lazy():
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client

def _norm(s: str) -> str:
    return (s or "").strip().lower()

def chat(user_text: str, session_id: str | None = None) -> str:
    """
    Primary entry point. Returns a single assistant reply as a string.
    Includes a simple 'parrot trap' (avoid echoing user verbatim).
    """
    # If no API key, fall back to a safe deterministic reply instead of crashing.
    if not os.getenv("OPENAI_API_KEY"):
        return "I’m here—tell me what you need and I’ll help."

    client = _client_lazy()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    messages = [
        {"role": "system",
         "content": "You are Chip, a Pure Storage virtual systems engineer. Be concise, helpful, and proactive."},
        {"role": "user", "content": user_text},
    ]

    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.6,
    )
    reply = (resp.choices[0].message.content or "").strip()
    if _norm(reply) == _norm(user_text):
        reply = "Got it—what outcome are you aiming for so I can help?"
    return reply or "I'm here—how can I help you next?"

# -----------------
# Back-compat shims
# -----------------
def generate_reply(prompt: str, *args, **kwargs) -> str:
    return chat(prompt, *args, **kwargs)

def generate_response(prompt: str, *args, **kwargs) -> str:
    return chat(prompt, *args, **kwargs)

def generate(prompt: str, *args, **kwargs) -> str:
    return chat(prompt, *args, **kwargs)

def complete(prompt: str, *args, **kwargs) -> str:
    return chat(prompt, *args, **kwargs)

# -----------------
# Greeting shim
# -----------------
def generate_greeting(
    name: str | None = None,
    region: str | None = None,
    role: str | None = None,
    company: str | None = None,
    profile: dict | None = None,
    **kwargs,
) -> str:
    """
    Backward-compatible helper used by /api/greet routes in legacy code.
    Works without OpenAI. If a profile dict is passed, we’ll pull fields from it.
    """
    # Extract from profile if provided
    if profile:
        name   = name   or profile.get("name") or profile.get("full_name") or profile.get("first_name")
        region = region or profile.get("region") or profile.get("geo")
        role   = role   or profile.get("role") or profile.get("title")
        company = company or profile.get("company")

    # Build a friendly, single-sentence greeting that matches your UI style
    parts = []
    if name:
        parts.append(name)
    if region:
        parts.append(f"in {region}")
    who = " ".join(parts).strip()

    if who:
        base = f"Hey—Chip here. What can I help you with today, {who}?"
    else:
        base = "Hey—Chip here. What can I help you with today?"

    # Optionally mention role/company if present
    extras = []
    if role:
        extras.append(role)
    if company:
        extras.append(company)
    if extras:
        base = base.rstrip("?") + f" ({', '.join(extras)})."

    return base
