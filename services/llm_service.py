
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
