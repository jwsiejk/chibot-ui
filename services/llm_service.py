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
    """Minimal, safe default chat call to OpenAI that returns a single string.

    - Model is configurable via OPENAI_MODEL (default: gpt-4o-mini)
    - Temperature via OPENAI_TEMPERATURE (default: 0.3)
    - Optional system prompt via CHIP_SYSTEM_PROMPT (kept short)
    """
    text = (user_text or "").strip()
    if not text:
        return "Tell me what you want to tackle and I’ll jump in."

    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
    try:
        temperature = float(os.getenv("OPENAI_TEMPERATURE", "0.3"))
    except Exception:
        temperature = 0.3
    system_prompt = os.getenv("CHIP_SYSTEM_PROMPT", "You are Chip, a helpful Pure Storage virtual systems engineer. Answer briefly and clearly.").strip()

    try:
        client = _client_lazy()
        resp = client.chat.completions.create(
            model=model,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
        )
        msg = (resp.choices[0].message.content or "").strip()
        return msg or "I didn’t catch that—want to try again?"
    except Exception:
        # Keep errors from bubbling into the UI; upstream code will log if needed.
        return "I'm having trouble generating a reply right now."

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
