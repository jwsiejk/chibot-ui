# conversation_orchestrator.py
# Orchestrated chat with per-session memory that reuses your existing LLM path.
# Drop-in: register the blueprint in app.py (see notes at bottom).

import time
from dataclasses import dataclass, field
from typing import List, Dict, Any

from flask import Blueprint, request, session, jsonify

# Use your existing DB helpers & user store
import memory

# ------------------------------ Session memory -------------------------------

@dataclass
class Msg:
    role: str
    content: str
    t: float = field(default_factory=time.time)

@dataclass
class SessionState:
    history: List[Msg] = field(default_factory=list)  # optional local echo (not required)
    product: str = ""   # FlashBlade | FlashArray | Portworx
    task: str = ""      # installation | design | troubleshooting | upgrade
    depth: str = ""     # high | deep
    last_seen: float = field(default_factory=time.time)

_SESS: Dict[str, SessionState] = {}
_TTL_SECONDS = 60 * 60  # 1 hour

def _sid() -> str:
    # Prefer authenticated email; fall back to remote addr
    return session.get("email") or request.remote_addr or "anon"

def _gc_sessions():
    now = time.time()
    for k in list(_SESS.keys()):
        if now - _SESS[k].last_seen > _TTL_SECONDS:
            _SESS.pop(k, None)

# ---------------------------- Lightweight heuristics -------------------------

def infer_from_text(ss: SessionState, text: str):
    t = (text or "").lower()

    # Product
    if any(k in t for k in ("flashblade", "flash blade", "fb", "s3 on flashblade")):
        ss.product = "FlashBlade"
    if any(k in t for k in ("flasharray", "flash array", "fa", "purity//fa", "purity/fa")):
        ss.product = "FlashArray"
    if "portworx" in t:
        ss.product = "Portworx"

    # Task
    if any(k in t for k in ("install", "installation", "set up", "setup", "deploy", "walk me through", "step by step")):
        ss.task = "installation"
    if any(k in t for k in ("design", "architecture", "size", "sizing", "capacity", "plan")):
        ss.task = "design"
    if any(k in t for k in ("troubleshoot", "error", "fail", "issue", "debug", "diagnose")):
        ss.task = "troubleshooting"
    if any(k in t for k in ("upgrade", "update", "patch")):
        ss.task = "upgrade"

    # Depth
    if "high level" in t or "overview" in t or "summary" in t:
        ss.depth = "high"
    if "step by step" in t or "walk" in t or "detailed" in t or "deep" in t:
        ss.depth = "deep"

def style_preamble(ss: SessionState) -> str:
    parts = [
        "You are Chip, a Pure Storage expert. Be 90% product substance, 10% light personality.",
        "Honor prior context. Continue the current product/task unless the user explicitly switches.",
        "Be concise. Use short sentences.",
        "If asked to 'walk me through it', give step-by-step: prerequisites, actions, validation.",
        "End with ONE short follow-up (e.g., 'Want the checklist emailed?' or 'Go deeper on step 2?').",
    ]
    if ss.product: parts.append(f"Product focus: {ss.product}.")
    if ss.task:    parts.append(f"Current task: {ss.task}.")
    if ss.depth:   parts.append(f"Depth: {ss.depth}.")
    return " [[ " + " ".join(parts) + " ]]"

def _to_chat_messages(hist, prompt=None) -> List[Dict[str, str]]:
    """
    Normalizes a heterogeneous list of items (as stored by memory.*) into
    OpenAI-style messages. Accepts dicts, tuples, or bare strings.
    """
    msgs: List[Dict[str, str]] = []
    if isinstance(hist, (list, tuple)):
        for item in hist:
            role = "user"; content = None
            if isinstance(item, dict):
                role = (item.get("role") or item.get("speaker") or "user")
                content = item.get("content") or item.get("message") or item.get("text")
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                role, content = item[0], item[1]
            elif isinstance(item, str):
                role, content = "user", item
            if content is None:
                continue
            msgs.append({"role": str(role), "content": str(content)})
    if prompt:
        msgs.append({"role": "user", "content": str(prompt)})
    return msgs

# ------------------------------- LLM call path --------------------------------
# Long-term choice: REUSE your existing services.llm_service.generate_reply so
# the orchestrator stays in lockstep with /api/chat (same model, safety, keys).
# This is how your current app.py talks to the model. :contentReference[oaicite:2]{index=2}

def call_llm(messages: List[Dict[str, str]]) -> str:
    """
    Call services.llm_service.generate_reply with best-effort signature matching.
    Tries messages/history/context_messages, then falls back to a flattened prompt.
    """
    try:
        from services.llm_service import generate_reply
    except Exception as e:
        raise RuntimeError(f"llm_service.generate_reply import failed: {e}")

    # 1) Try chat-style kwargs
    for kw in ("messages", "history", "context_messages", "conversation", "context"):
        try:
            return generate_reply(**{kw: messages})
        except TypeError:
            continue
        except Exception as e:
            raise RuntimeError(f"generate_reply({kw}=...) failed: {e}")

    # 2) Fallback: flatten messages (include system)
    flat = "\n".join(f"{m.get('role','user')}: {m.get('content','')}" for m in messages if m.get("content"))
    try:
        return generate_reply(flat)
    except Exception as e:
        raise RuntimeError(f"generate_reply(prompt) failed: {e}")

# --------------------------------- Blueprint ----------------------------------

bp = Blueprint("conversation_orchestrator", __name__)

@bp.post("/api/chat_orchestrated")
def chat_orchestrated():
    # Require auth (same behavior as /api/chat)
    if not session.get("email"):
        return jsonify({"ok": False, "error": "Not authenticated"}), 401

    data = request.get_json(silent=True) or {}
    text = (data.get("text") or data.get("prompt") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "Prompt required"}), 400

    key = _sid()
    ss = _SESS.get(key) or SessionState()
    _SESS[key] = ss
    ss.last_seen = time.time()
    _gc_sessions()

    # Pull a small slice of server-side history (DB is the ground truth)
    try:
        hist = memory.get_recent_conversation(session["email"], limit=8)
    except Exception:
        hist = []

    # Update memory from the user's new turn (product/task/depth)
    infer_from_text(ss, text)

    # Build messages: style preamble + recent history + new user turn
    sys_msg = {"role": "system", "content": style_preamble(ss)}
    msgs = [sys_msg] + _to_chat_messages(hist, prompt=text)

    # Generate using your existing model path
    try:
        reply = call_llm(msgs) or ""
    except Exception as e:
        return jsonify({"ok": False, "error": f"llm_error: {e}"}), 500

    # Persist the turn in DB history and refresh memory from assistant reply
    try:
        memory.log_conversation(email=session["email"], role="user", message=text)
        memory.log_conversation(email=session["email"], role="assistant", message=reply)
    except Exception:
        pass

    infer_from_text(ss, reply)
    ss.last_seen = time.time()

    return jsonify({"ok": True, "reply": reply, "state": {
        "product": ss.product, "task": ss.task, "depth": ss.depth
    }})
