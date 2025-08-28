# routes/conversation.py
from __future__ import annotations

from flask import Blueprint, request, jsonify, session, Response
import json
import time

# --- optional imports with safe fallbacks ------------------------------------
try:
    import memory
except Exception:  # pragma: no cover
    class _MemFallback:
        def get_recent_conversation(self, *_a, **_k):
            return []
        def get_user(self, *_a, **_k):
            return {}
    memory = _MemFallback()

try:
    from utils.call_log import call_log
except Exception:  # pragma: no cover
    class _NoopLog:
        def add(self, *args, **kwargs):  # keep signature flexible
            pass
    call_log = _NoopLog()

try:
    from utils.text import ensure_text as _ensure_text
except Exception:  # pragma: no cover
    def _ensure_text(obj):
        """
        Best-effort conversion of arbitrary objects (including generators,
        iterables, Flask Response) to a plain string.
        """
        if obj is None:
            return ""
        if isinstance(obj, str):
            return obj
        if isinstance(obj, bytes):
            try:
                return obj.decode("utf-8", errors="ignore")
            except Exception:
                return ""
        # Flask Response with iterable body
        if isinstance(obj, Response):
            try:
                # Prefer the iterable if present
                if hasattr(obj, "response") and obj.response is not None:
                    return "".join(_ensure_text(chunk) for chunk in obj.response)
                return obj.get_data(as_text=True)
            except Exception:
                return ""
        # Mapping/dict – pick common text fields
        if isinstance(obj, dict):
            return str(
                obj.get("text")
                or obj.get("message")
                or obj.get("reply")
                or obj.get("content")
                or ""
            )
        # Generic iterables/generators (but not str/bytes handled above)
        try:
            it = iter(obj)  # may raise TypeError for non-iterables
            return "".join(_ensure_text(x) for x in it)
        except TypeError:
            pass
        except Exception:
            # If it looked iterable but broke mid-way, best-effort str()
            try:
                return "".join(str(x) for x in obj)
            except Exception:
                pass
        # Fallback
        try:
            return str(obj)
        except Exception:
            return ""

# Primary LLM entrypoint
try:
    from services.llm_service import generate_response
except Exception as _e:  # pragma: no cover
    # Keep the app usable even if the LLM module cannot import.
    def generate_response(text: str, history=None):
        yield f"(fallback) You said: {text}"

# ------------------------------------------------------------------------------

conversation_bp = Blueprint("conversation", __name__, url_prefix="/api")


def _db_history():
    """Fetch recent history for the logged-in user; tolerate failures."""
    try:
        email = session.get("email")
        if not email:
            return []
        return memory.get_recent_conversation(email, limit=10)
    except Exception:
        return []


def _ok_payload(resp) -> dict:
    """Normalize any response (including generators) to a JSON-friendly dict."""
    try:
        text = _ensure_text(resp)
    except Exception:
        text = ""
    text = (text or "").strip()
    return {"ok": True, "text": text, "reply": text, "message": text}


def _extract_text_and_history():
    data = request.get_json(silent=True) or {}
    text = (
        data.get("message")
        or data.get("text")
        or data.get("prompt")
        or request.args.get("q")
        or ""
    ).strip()

    history = data.get("history") or data.get("messages") or _db_history() or []
    if not isinstance(history, (list, tuple)):
        history = []
    return text, history


def _safe_orchestrate(text: str, history):
    call_log.add("orchestrator", "request", size=len(text or ""), history=len(history or []))
    try:
        # Generate the assistant response (may return a generator/iterable/str)
        resp = generate_response(text, history=history)
        body = _ok_payload(resp)
        call_log.add("orchestrator", "ok")
        return jsonify(body), 200
    except Exception as e:
        call_log.add("orchestrator", "error", error=str(e))
        fallback = _ok_payload(
            "I hit a snag but I’m ready to continue. Want a quick overview or step‑by‑step?"
        )
        return jsonify(fallback), 200


def _preflight():
    # Minimal CORS preflight handler (Flask/CORS may also be configured globally)
    return ("", 204)


# --- Routes (aliases maintained for backwards compatibility) -----------------

@conversation_bp.route("/conversation", methods=["GET", "POST", "OPTIONS"])
@conversation_bp.route("/orchestrator", methods=["GET", "POST", "OPTIONS"])
@conversation_bp.route("/chat", methods=["GET", "POST", "OPTIONS"])
def orchestrator_route():
    if request.method == "OPTIONS":
        return _preflight()

    text, history = _extract_text_and_history()

    # Return a helpful nudge if no input provided (GET ping or empty POST)
    if not text:
        call_log.add("orchestrator", "empty_input")
        return jsonify(_ok_payload("Hi! What would you like to talk about?")), 200

    return _safe_orchestrate(text, history)
