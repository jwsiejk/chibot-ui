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
    Returns a single assistant reply (string). Contains a parrot trap:
    if the model replies with the same text as the user, we substitute a safe prompt.
    """
    client = _client_lazy()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")  # adjust as needed

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

    # Parrot trap here as well, so any route calling llm_service.chat is safe
    if _norm(reply) == _norm(user_text):
        reply = "Got it. What outcome are you aiming for so I can help?"

    return reply or "I'm here—how can I help you next?"
