# services/llm_service.py
import os
from typing import List, Dict, Optional

# --- Chip persona (fallback if persona.txt not present) ---
DEFAULT_PERSONA = """
You are **Chip**, a virtual Pure Storage solutions engineer.
Tone: Nebraska plain‑spoken, warm, practical, unintentionally funny. Be concise and teachy.
Style: clear steps, minimal fluff. Prefer everyday language over buzzwords.
Personality: occasionally drop a gentle Nebraska-ism (“reckon”, “fair to middlin’”), no more than once every few turns.
Behavior: answer directly first, then (sometimes) offer a short follow‑up option that invites the user to continue.
Boundaries: don’t invent facts; if unsure, say what you’d check next.
""".strip()

def _load_persona_text() -> str:
    # Allow override from static/chip/persona.txt if present
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    persona_path = os.path.join(here, "static", "chip", "persona.txt")
    try:
        with open(persona_path, "r", encoding="utf-8") as f:
            txt = f.read().strip()
            # Some older copies contained ellipses placeholders; ignore them
            txt = txt.replace("\u2026", "...")
            return txt or DEFAULT_PERSONA
    except Exception:
        return DEFAULT_PERSONA

PERSONA_CACHE = _load_persona_text()

def _build_system_prompt(profile: Optional[Dict]=None) -> str:
    name = (profile or {}).get("name") or "friend"
    role = (profile or {}).get("title") or ""
    region = (profile or {}).get("region") or ""
    extra = []
    if role:
        extra.append(f"User role/title: {role}.")
    if region:
        extra.append(f"User region: {region}.")
    extra.append("Speak as Chip. Keep it personable and practical.")
    return PERSONA_CACHE + "\n\n" + " ".join(extra)

def _to_history(context_messages: Optional[List[Dict]]) -> List[Dict[str,str]]:
    msgs: List[Dict[str,str]] = []
    for m in (context_messages or []):
        role = m.get("role") or m.get("speaker") or "user"
        content = m.get("message") or m.get("content") or ""
        role = "assistant" if role.lower().startswith("assist") else "user"
        if content:
            msgs.append({"role": role, "content": content})
    return msgs

def generate_reply(prompt: str, *, profile: Optional[Dict]=None, context_messages: Optional[List[Dict]]=None,
                   model: Optional[str]=None, max_tokens: int=220, temperature: float=0.6) -> str:
    """Generate a Chip‑style reply using OpenAI Chat Completions."""
    api_key = os.getenv("OPENAI_API_KEY")
    model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    if not api_key:
        return "Chip is up, but OPENAI_API_KEY is not set. Add it to your environment."

    system_prompt = _build_system_prompt(profile)
    history = _to_history(context_messages)

    # Special-case: if the prompt is a greet intent from the UI
    if prompt.strip().lower() in {"greet", "hello", "hi", "start"}:
        prompt = ("Greet the user by name if provided. Two short sentences max. "
                  "Invite them to start with a specific topic—avoid generic 'How can I help?'.") 

    messages: List[Dict[str,str]] = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": prompt})

    # Try modern SDK first
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        # Fallback to old SDK
        try:
            import openai as openai_legacy
            openai_legacy.api_key = api_key
            resp = openai_legacy.ChatCompletion.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return (resp["choices"][0]["message"]["content"] or "").strip()
        except Exception as e:
            return f"LLM call failed: {e}"
