# services/llm_service.py
import os

def generate_reply(messages, model=None, max_tokens=256, temperature=0.6):
    """
    messages: list of {"role": "...", "content": "..."}
    returns: string reply
    """
    api_key = os.getenv("OPENAI_API_KEY")
    model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    if not api_key:
        return "Chip is up, but OPENAI_API_KEY is not set. Add it to your environment."

    # Try the modern OpenAI client first
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
        # Fallback for older 'openai' packages (pre-1.0)
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
