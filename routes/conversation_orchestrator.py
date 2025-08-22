# conversation_orchestrator.py
import os, time
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any
from flask import Blueprint, request, session, jsonify

# ---- In-memory store (swap for Redis if you prefer) ----
_STORE: Dict[str, "SessionState"] = {}
TTL_SECONDS = 60 * 60  # 1 hour

@dataclass
class Msg:
    role: str
    content: str
    t: float = field(default_factory=time.time)

@dataclass
class SessionState:
    history: List[Msg] = field(default_factory=list)
    product: str = ""   # FlashBlade | FlashArray | Portworx
    task: str = ""      # installation | design | troubleshooting | upgrade
    depth: str = ""     # high | deep
    goal: str = ""      # optional "what are we trying to achieve"
    last_seen: float = field(default_factory=time.time)

def _sid():
    return session.get("sid") or request.cookies.get("session") or request.remote_addr

def _touch(ss: SessionState):
    ss.last_seen = time.time()

# ---- Lightweight heuristics (fast, predictable) ----
def infer_from_text(ss: SessionState, text: str):
    t = (text or "").lower()
    if any(k in t for k in ("flashblade", "flash blade", "fb", "s3 on flashblade")):
        ss.product = "FlashBlade"
    if any(k in t for k in ("flasharray", "flash array", "fa", "purity//fa")):
        ss.product = "FlashArray"
    if "portworx" in t: ss.product = "Portworx"

    if any(k in t for k in ("install", "installation", "set up", "setup", "deploy", "walk me through")):
        ss.task = "installation"
    if any(k in t for k in ("design", "architecture", "size", "sizing", "capacity", "plan")):
        ss.task = "design"
    if any(k in t for k in ("troubleshoot", "error", "fail", "issue", "debug", "diagnose")):
        ss.task = "troubleshooting"
    if any(k in t for k in ("upgrade", "update", "patch")):
        ss.task = "upgrade"

    if "high level" in t or "overview" in t: ss.depth = "high"
    if "step by step" in t or "walk" in t or "detailed" in t or "deep" in t: ss.depth = "deep"

def style_preamble(ss: SessionState) -> str:
    parts = [
        "You are Chip, a Pure Storage expert. Be 90% product substance, 10% light personality.",
        "Honor prior context. Continue the current product/task unless the user explicitly switches.",
        "Be concise. Use short sentences. If asked to 'walk me through it', give step-by-step: prereqs, actions, validation.",
        "End with ONE short follow-up to keep momentum (e.g., 'Want the checklist emailed?' or 'Go deeper on step 2?').",
    ]
    if ss.product: parts.append(f"Product focus: {ss.product}.")
    if ss.task:    parts.append(f"Current task: {ss.task}.")
    if ss.depth:   parts.append(f"Depth: {ss.depth}.")
    return " [[ " + " ".join(parts) + " ]]"

def trim_history(ss: SessionState, max_chars=8000):
    # simple budget to avoid overly long prompts
    total = 0
    out = []
    for m in reversed(ss.history):
        total += len(m.content)
        out.append(m)
        if total > max_chars:
            break
    ss.history = list(reversed(out))

# ---- Model call (use your existing OpenAI/OpenRouter client) ----
def call_llm(messages: List[Dict[str, str]]) -> str:
    # Replace with your existing model client. Example with OpenAI:
    # from openai import OpenAI
    # client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    # chat = client.chat.completions.create(model=os.getenv("OPENAI_MODEL","gpt-4o-mini"), messages=messages, temperature=0.4)
    # return chat.choices[0].message.content
    raise RuntimeError("Hook up your model client here (use the same one /api/chat uses).")

bp = Blueprint("convo_orchestrator", __name__)

@bp.post("/api/chat_orchestrated")
def chat_orchestrated():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or data.get("prompt") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "empty prompt"}), 400

    sid = _sid()
    ss = _STORE.get(sid) or SessionState()
    _STORE[sid] = ss
    _touch(ss)

    # Update memory from the user's turn
    infer_from_text(ss, text)
    ss.history.append(Msg("user", text))
    trim_history(ss)

    # Build prompt with memory + style
    sys = style_preamble(ss)
    messages = [{"role": "system", "content": sys}]
    for m in ss.history:
        messages.append({"role": m.role, "content": m.content})

    # Generate
    try:
        reply = call_llm(messages)
    except Exception as e:
        return jsonify({"ok": False, "error": f"llm_error: {e}"}), 500

    # Keep reply + refresh memory (the assistant may name the product explicitly)
    ss.history.append(Msg("assistant", reply))
    infer_from_text(ss, reply)
    trim_history(ss)

    # Small state projection for the client (debug/telemetry)
    state = {k: getattr(ss, k) for k in ("product", "task", "depth")}
    return jsonify({"ok": True, "reply": reply, "state": state})
