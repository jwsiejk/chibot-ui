import os

def cap_30_words(s: str) -> str:
    words = (s or "").split()
    return " ".join(words[:30])

def fallback(prompt: str) -> str:
    base = ("I'm Chip—your quiet, capable SE. I keep it short. "
            "What's your goal? I can explain steps, gotchas, and why it matters.")
    if not prompt:
        return cap_30_words(base)
    return cap_30_words(f"{prompt.strip()} — Here’s the straight path and one gotcha to watch.")

def reply(messages):
    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    if not api_key:
        return None
    try:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.3,
                max_tokens=180,
            )
            return cap_30_words(resp.choices[0].message.content.strip())
        except Exception:
            import openai
            openai.api_key = api_key
            resp = openai.ChatCompletion.create(
                model=model,
                messages=messages,
                temperature=0.3,
                max_tokens=180,
            )
            return cap_30_words(resp["choices"][0]["message"]["content"].strip())
    except Exception:
        return None
