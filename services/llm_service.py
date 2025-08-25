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
    Includes a 'parrot trap' to avoid echoing the user verbatim.
    """
    client = _client_lazy()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")  # set your preferred default

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
        reply = "Got it. Tell me a bit more about what you need so I can help."
    return reply or "I'm here—how can I help you next?"

# --- Backward-compatibility shims (keep old imports working) ---
def generate_reply(prompt: str, *args, **kwargs) -> str:
    return chat(prompt, *args, **kwargs)

def generate_response(prompt: str, *args, **kwargs) -> str:
    return chat(prompt, *args, **kwargs)

# Some codebases use very generic names:
def generate(prompt: str, *args, **kwargs) -> str:
    return chat(prompt, *args, **kwargs)

def complete(prompt: str, *args, **kwargs) -> str:
    return chat(prompt, *args, **kwargs)
