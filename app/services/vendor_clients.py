# app/services/vendor_clients.py
import os

def make_openai_client():
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set — cannot create OpenAI client.")
    try:
        from openai import OpenAI  # type: ignore
    except Exception as e:
        raise RuntimeError("openai SDK not installed; add 'openai>=1.0.0' to requirements.txt") from e
    return OpenAI(api_key=key)
