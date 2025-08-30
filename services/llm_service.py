# services/llm_service.py
# Robust, no-regressions LLM helpers for Ask Chip (text + greeting)
# - Prefers the official OpenAI SDK (>=1.x).
# - Falls back to the legacy SDK if needed.
# - If both fail or no API key is present, falls back to services.openai_fallback.
import os
from .folksy import inject as inject_folksy
from typing import Optional, Dict, Any, Generator, Iterable, Union

try:
    from openai import OpenAI  # new SDK (>=1.x)
except Exception:
    OpenAI = None  # type: ignore

# Lazy client holder (new SDK only)
_client = None

def _client_lazy():
    global _client
    if _client is None and OpenAI is not None:
        api_key = os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_APIKEY") or os.getenv("OPENAI_TOKEN")
        _client = OpenAI(api_key=api_key)  # None is fine; will error on use if missing
    return _client

def _norm(s: str) -> str:
    return (s or "").strip()

def _model() -> str:
    return os.getenv("OPENAI_MODEL") or "gpt-4o-mini"

def _system_prompt() -> str:
    # Keep this short; domain behavior is layered elsewhere by the app.
    return "You are Chip, a friendly, concise virtual systems engineer. Keep replies short unless asked for depth."

def _ensure_messages(prompt_or_messages: Union[str, Iterable[Dict[str, Any]]], system: Optional[str] = None) -> list[dict]:
    """Normalize input into an OpenAI messages list and ensure a system prompt exists."""
    sys = _norm(system) or _system_prompt()
    if isinstance(prompt_or_messages, str):
        return [{"role": "system", "content": sys}, {"role": "user", "content": prompt_or_messages}]
    # Copy to list and ensure proper shape
    msgs = list(prompt_or_messages or [])
    has_system = any((m or {}).get("role") == "system" for m in msgs)
    if not has_system:
        msgs.insert(0, {"role": "system", "content": sys})
    return msgs

def _chat_via_new_sdk(messages: list[dict]) -> Optional[str]:
    client = _client_lazy()
    if client is None:
        return None
    # Try both modern endpoints for best compatibility
    try:
        # Preferred path: chat.completions (stable for text)
        r = client.chat.completions.create(model=_model(), messages=messages, temperature=0.6)
        txt = (r.choices and r.choices[0].message and r.choices[0].message.content) or ""
        return _norm(txt)
    except Exception:
        pass
    try:
        # Alternate: responses API
        r = client.responses.create(model=_model(), input=messages)
        # responses output handling
        try:
            # SDKs often expose convenience .output_text
            txt = getattr(r, "output_text", None)
            if txt:
                return _norm(txt)
        except Exception:
            pass
        # Fallback: stitch text segments
        parts = []
        for item in getattr(r, "output", []) or []:
            if isinstance(item, dict):
                if item.get("type") == "message":
                    for c in item.get("content", []) or []:
                        if isinstance(c, dict) and c.get("type") == "output_text":
                            parts.append(c.get("text") or "")
        return _norm(" ".join(parts))
    except Exception:
        # Last resort: try legacy SDK if installed
        pass
    return None

def _stream_via_new_sdk(messages: list[dict], temperature: float = 0.6, **kwargs) -> Optional[Generator[str, None, None]]:
    """Stream tokens via the new SDK if available. Returns a generator or None."""
    client = _client_lazy()
    if client is None:
        return None
    # Try streaming first; fall back to non-stream single-yield
    def _gen() -> Generator[str, None, None]:
        try:
            resp = client.chat.completions.create(
                model=_model(),
                messages=messages,
                temperature=temperature,
                stream=True,
                **{k: v for k, v in kwargs.items() if k in ("max_tokens", "top_p", "frequency_penalty", "presence_penalty")},
            )
            for chunk in resp:
                try:
                    delta = chunk.choices[0].delta.content  # type: ignore[attr-defined]
                except Exception:
                    delta = None
                if delta:
                    yield delta
            return
        except Exception:
            # Fallback to one-shot
            try:
                r = client.chat.completions.create(
                    model=_model(),
                    messages=messages,
                    temperature=temperature,
                    **{k: v for k, v in kwargs.items() if k in ("max_tokens", "top_p", "frequency_penalty", "presence_penalty")},
                )
                txt = (r.choices and r.choices[0].message and r.choices[0].message.content) or ""
                txt = _norm(txt)
                if txt:
                    yield txt
                return
            except Exception:
                return
    return _gen()

def _chat_via_legacy_sdk(user_text: str) -> Optional[str]:
    # Lazy import legacy SDK only if present
    try:
        import openai as oai  # legacy SDK
    except Exception:
        return None
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_APIKEY") or os.getenv("OPENAI_TOKEN")
    if not (api_key and hasattr(oai, "ChatCompletion")):
        return None
    try:
        oai.api_key = api_key
        r = oai.ChatCompletion.create(
            model=_model(),
            messages=[{"role":"system","content":_system_prompt()},
                      {"role":"user","content":user_text}],
            temperature=0.6,
        )
        txt = r["choices"][0]["message"]["content"] or ""
        return _norm(txt)
    except Exception:
        return None

def _stream_via_legacy_sdk(messages: list[dict], temperature: float = 0.6, **kwargs) -> Optional[Generator[str, None, None]]:
    """Stream tokens via the legacy SDK if present. Returns a generator or None."""
    try:
        import openai as oai  # legacy SDK
    except Exception:
        return None
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_APIKEY") or os.getenv("OPENAI_TOKEN")
    if not (api_key and hasattr(oai, "ChatCompletion")):
        return None

    def _gen() -> Generator[str, None, None]:
        try:
            resp = oai.ChatCompletion.create(
                model=_model(),
                messages=messages,
                temperature=temperature,
                stream=True,
                **{k: v for k, v in kwargs.items() if k in ("max_tokens", "top_p", "frequency_penalty", "presence_penalty")},
            )
            for chunk in resp:
                try:
                    delta = chunk["choices"][0]["delta"].get("content", "")
                except Exception:
                    delta = ""
                if delta:
                    yield delta
            return
        except Exception:
            # Fallback to one-shot legacy call
            try:
                r = oai.ChatCompletion.create(
                    model=_model(),
                    messages=messages,
                    temperature=temperature,
                    **{k: v for k, v in kwargs.items() if k in ("max_tokens", "top_p", "frequency_penalty", "presence_penalty")},
                )
                txt = (r["choices"][0]["message"]["content"] or "").strip()
                if txt:
                    yield txt
                return
            except Exception:
                return
    return _gen()

def chat(user_text: str, session_id: str | None = None) -> str:
    """Return an assistant reply for the given user_text.
    - Tries the modern OpenAI SDK.
    - Falls back to legacy.
    - Finally falls back to services.openai_fallback.generate_reply.
    """
    user_text = _norm(user_text)
    if not user_text:
        return ""
    # New SDK with messages
    messages = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": user_text},
    ]
    txt = _chat_via_new_sdk(messages)
    if txt:
        return txt
    txt = _chat_via_legacy_sdk(user_text)
    if txt:
        return txt
    # Final fallback: local echo helper
    try:
        from .openai_fallback import generate_reply as _fallback  # relative import ok
    except Exception:
        try:
            # absolute import path if services is a package root
            from services.openai_fallback import generate_reply as _fallback  # type: ignore
        except Exception:
            _fallback = None  # type: ignore
    if _fallback:
        msg, _err = _fallback(user_text)
        return _norm(msg)
    return ""

def generate_greeting(profile: Optional[Dict[str, Any]] = None) -> str:
    """Make a short, natural greeting. Uses profile (name/title) if available.
    Always returns a string; falls back to a safe default if LLMs are unavailable.
    """
    name = _norm((profile or {}).get("name") or "")
    title = _norm((profile or {}).get("title") or "")
    # Construct a tiny prompt—keep it deterministic enough to avoid long rambles.
    user_bits = []
    if name:
        user_bits.append(f"for {name}")
    if title:
        user_bits.append(f"({title})")
    ctx = " ".join(user_bits).strip()
    prompt = (
        "Write one short friendly greeting as Chip, a helpful virtual systems engineer. "
        + (f"Tailor it {ctx}. " if ctx else "")
        + "Keep it under 18 words. End with a question that invites the user to start."
    )
    # Try via the same pathways as chat()
    messages = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": prompt},
    ]
    txt = _chat_via_new_sdk(messages)
    if not txt:
        txt = _chat_via_legacy_sdk(prompt)
    if not txt:
        # Final fallback template
        if name:
            return f"Hey {name}—Chip here. What should we tackle first?"
        return "Hey—Chip here. What should we tackle first?"
    # Light Nebraska personality (optional)
    try:
        txt, _used = inject_folksy(txt, prompt)
    except Exception:
        pass

    # Clean up any stray quotes/markdown the model might add
    return _norm(txt).strip().strip('"').strip("'")

def generate_response(prompt_or_messages: Union[str, Iterable[Dict[str, Any]]],
                      session_id: str | None = None,
                      **kwargs) -> Generator[str, None, None]:
    """Stream a response as an iterator of text chunks.
    Backwards-compatible shim for the conversation blueprint which expects a generator.
    Usage patterns supported:
      - generate_response("hello")
      - generate_response([{'role':'user','content':'hello'}])
      - generate_response([...], max_tokens=512)
    If streaming is unavailable, yields a single full-string chunk.
    """
    messages = _ensure_messages(prompt_or_messages, system=kwargs.pop("system", None))
    temperature = float(os.getenv("OPENAI_T", "0.6"))
    # 1) Try modern SDK streaming
    gen = _stream_via_new_sdk(messages, temperature=temperature, **kwargs)
    if gen is not None:
        yield from gen
        return
    # 2) Try legacy SDK streaming
    gen = _stream_via_legacy_sdk(messages, temperature=temperature, **kwargs)
    if gen is not None:
        yield from gen
        return
    # 3) Final fallback: non-network local echo helper
    # Reuse chat() to produce a single message and yield once.
    try:
        text = chat(_norm(messages[-1].get("content") if messages else ""))
    except Exception:
        text = ""
    if text:
        yield text
    else:
        # Last-resort safety so callers don't hang
        yield "Sorry—I'm having trouble responding right now."
