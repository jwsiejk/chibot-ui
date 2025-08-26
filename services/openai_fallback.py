# services/openai_fallback.py
import os, json, traceback

def generate_reply(user_text: str) -> (str, str):
    """Return (reply_text, error) where error is None on success.
    Tries OpenAI; if not available, falls back to a simple echo/guide.
    """
    user_text = (user_text or '').strip()
    if not user_text:
        return '', 'empty_input'

    # Try OpenAI if possible
    api_key = os.getenv('OPENAI_API_KEY') or os.getenv('OPENAI_APIKEY') or os.getenv('OPENAI_TOKEN')
    if api_key:
        try:
            try:
                # Prefer new SDK
                from openai import OpenAI
                client = OpenAI(api_key=api_key)
                # Model is configurable, default to 'gpt-4o-mini' if unset
                model = os.getenv('OPENAI_MODEL') or 'gpt-4o-mini'
                msg = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role":"system","content":"You are Chip, a friendly, concise virtual systems engineer."},
                        {"role":"user","content":user_text}
                    ],
                    temperature=0.6,
                )
                txt = (msg.choices[0].message.content or '').strip()
                if txt:
                    return txt, None
            except Exception:
                # Try legacy SDK
                import openai as oai
                oai.api_key = api_key
                model = os.getenv('OPENAI_MODEL') or 'gpt-4o-mini'
                msg = oai.ChatCompletion.create(
                    model=model,
                    messages=[
                        {"role":"system","content":"You are Chip, a friendly, concise virtual systems engineer."},
                        {"role":"user","content":user_text}
                    ],
                    temperature=0.6,
                )
                txt = (msg['choices'][0]['message']['content'] or '').strip()
                if txt:
                    return txt, None
        except Exception as e:
            # fall through to echo
            err = f"openai_error: {type(e).__name__}: {e}"
            return _fallback_reply(user_text), err

    # No API key or failure: echo fallback
    return _fallback_reply(user_text), None

def _fallback_reply(user_text: str) -> str:
    return f"You said: '{user_text}'. I'm running in fallback mode right now, but I can still guide you step-by-step."
