import os

def _cap_30_words(s: str) -> str:
    words = (s or "").split()
    return " ".join(words[:30])

def _persona_system():
    return (
        "You are Chip, a virtual systems engineer for Pure Storage. "
        "Speak in 1–2 crisp sentences. Be practical, calm, and specific. "
        "Never exceed 30 words. Avoid marketing fluff. Sound like a well‑mannered Nebraskan."
    )

def _profile_to_context(profile: dict) -> str:
    if not profile:
        return ""
    bits = [profile.get("name") or "", profile.get("title") or "", profile.get("region") or ""]
    bits = [b for b in bits if b]
    return "User profile: " + ", ".join(bits) if bits else ""

def generate_reply(prompt: str, profile: dict=None, context_messages=None) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    system = _persona_system()
    msgs = [{"role":"system","content": system}]

    prof_line = _profile_to_context(profile)
    if prof_line:
        msgs.append({"role":"system","content": prof_line})

    if context_messages:
        for m in context_messages[-8:]:
            role = "assistant" if m.get("role")=="assistant" else "user"
            msgs.append({"role": role, "content": m.get("message","")})

    msgs.append({"role":"user","content": prompt or ""})

    if not api_key:
        base = "I'm Chip. Here's the straight path and a gotcha to watch."
        return _cap_30_words(f"{(prompt or '').strip()} — {base}")
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(model=model, messages=msgs, temperature=0.2, max_tokens=180)
        txt = (resp.choices[0].message.content or "").strip()
        return _cap_30_words(txt)
    except Exception:
        try:
            import openai
            openai.api_key = api_key
            resp = openai.ChatCompletion.create(model=model, messages=msgs, temperature=0.2, max_tokens=180)
            txt = (resp["choices"][0]["message"]["content"] or "").strip()
            return _cap_30_words(txt)
        except Exception:
            return _cap_30_words(f"{prompt or 'Okay.'} — Here's the straight path and one gotcha.")
