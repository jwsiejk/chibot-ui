# services/llm_service.py
import os
from typing import List, Dict, Any, Optional

from openai import OpenAI

_client = None

def _client_lazy():
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client

def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()

def _coerce_messages(
    user_text: Optional[str] = None,
    messages: Optional[List[Dict[str, Any]]] = None,
    history: Optional[List[Dict[str, Any]]] = None,
    prompt: Optional[str] = None,
) -> List[Dict[str, str]]:
    """
    Normalize various inputs into OpenAI chat 'messages' format.
    """
    out: List[Dict[str, str]] = []
    if messages and isinstance(messages, (list, tuple)):
        for m in messages:
            role = (m.get("role") or "user").strip()
            content = str(m.get("content") or "")
            if content:
                out.append({"role": role, "content": content})
    else:
        # Build from history + user_text / prompt
        if history and isinstance(history, (list, tuple)):
            for m in history:
                role = (m.get("role") or "user").strip()
                content = str(m.get("content") or "")
                if content:
                    out.append({"role": role, "content": content})
        final_user = user_text or prompt
        if final_user:
            out.append({"role": "user", "content": str(final_user)})

    if not out:
        out = [{"role": "user", "content": str(user_text or prompt or "Hello")}]
    return out

def _default_system() -> str:
    return "You are Chip, a Pure Storage virtual systems engineer. Be concise, helpful, and proactive."

def chat(
    user_text: str,
    session_id: Optional[str] = None,
    *,
    temperature: Optional[float] = 0.6,
    max_tokens: Optional[int] = None,
) -> str:
    """Return a single assistant reply. Protects against echoing the user."""
    client = _client_lazy()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    messages = [
        {"role": "system", "content": _default_system()},
        {"role": "user", "content": user_text},
    ]

    params = {
        "model": model,
        "messages": messages,
        "temperature": temperature if temperature is not None else 0.6,
    }
    if max_tokens:
        params["max_tokens"] = max_tokens

    resp = client.chat.completions.create(**params)
    reply = (resp.choices[0].message.content or "").strip()

    # Parrot trap
    if _norm(reply) == _norm(user_text):
        reply = "Got it. What outcome do you want so I can help?"

    return reply or "I'm here—how can I help you next?"

# --- Backward-compatibility shims (keep old imports working) ---

def generate_reply(
    prompt: Optional[str] = None,
    *,
    messages: Optional[List[Dict[str, Any]]] = None,
    history: Optional[List[Dict[str, Any]]] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = 0.6,
    **kwargs,
) -> str:
    """Compatibility API used across the codebase.

    Accepts either:
    - prompt/user_text, or
    - messages=[{role, content}, ...], or
    - history=[...]+prompt
    """
    # Build the message list if provided; prefer messages/history style for richer context
    msgs = _coerce_messages(user_text=prompt, messages=messages, history=history, prompt=prompt)
    client = _client_lazy()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    params = {
        "model": model,
        "messages": [{"role": "system", "content": _default_system()}, *msgs],
        "temperature": temperature if temperature is not None else 0.6,
    }
    if max_tokens:
        params["max_tokens"] = max_tokens

    resp = client.chat.completions.create(**params)
    reply = (resp.choices[0].message.content or "").strip()

    # Parrot trap compares to the *last user message* if available
    try:
        last_user_text = ""
        for m in reversed(msgs):
            if m.get("role") == "user":
                last_user_text = m.get("content") or ""
                break
        if _norm(reply) == _norm(last_user_text):
            reply = "Understood. Could you share the goal so I can tailor the next step?"
    except Exception:
        pass

    return reply or "I'm here—how can I help you next?"

def generate_response(prompt: str, *args, **kwargs) -> str:
    # Some modules call this name; forward to generate_reply
    return generate_reply(prompt, *args, **kwargs)

# Generic names occasionally used
def generate(prompt: str, *args, **kwargs) -> str:
    return generate_reply(prompt, *args, **kwargs)

def complete(prompt: str, *args, **kwargs) -> str:
    return generate_reply(prompt, *args, **kwargs)
