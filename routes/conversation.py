# conversation_orchestrator.py
# Orchestrated chat with per-session memory that reuses your existing LLM path.

import time, re
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

from flask import Blueprint, request, session, jsonify

import memory
from services.email_service import send_email  # SMTP already configured in your env :contentReference[oaicite:5]{index=5}
try:
    from services.accounts_service import search_accounts
except Exception:
    def search_accounts(*args, **kwargs): return []

# ------------------------------ Session memory -------------------------------

@dataclass
class Msg:
    role: str
    content: str
    t: float = field(default_factory=time.time)

@dataclass
class SessionState:
    history: List[Msg] = field(default_factory=list)  # optional local echo
    product: str = ""   # FlashBlade | FlashArray | Portworx
    task: str = ""      # installation | design | troubleshooting | upgrade
    depth: str = ""     # high | deep
    last_assistant: str = ""
    last_seen: float = field(default_factory=time.time)

_SESS: Dict[str, SessionState] = {}
_TTL_SECONDS = 60 * 60  # 1 hour

def _sid() -> str:
    return session.get("email") or request.remote_addr or "anon"

def _gc_sessions():
    now = time.time()
    for k in list(_SESS.keys()):
        if now - _SESS[k].last_seen > _TTL_SECONDS:
            _SESS.pop(k, None)

# ---------------------------- Lightweight heuristics -------------------------

def _norm_product_terms(txt: str) -> str:
    """Hard normalize common mis-hearings to a product term."""
    t = (txt or "").lower()
    # 'flash light' / 'flashlight' / 'flash blade'  → FlashBlade
    if re.search(r"\bflash\s*light\b", t) or "flashlight" in t:
        return "FlashBlade"
    if re.search(r"\bflash\s*blade\b", t) or "flashblade" in t:
        return "FlashBlade"
    if re.search(r"\bflash\s*array\b", t) or "flasharray" in t or "purity//fa" in t or "purity/fa" in t:
        return "FlashArray"
    if "portworx" in t:
        return "Portworx"
    return ""

def infer_from_text(ss: SessionState, text: str):
    t = (text or "").lower()
    prod = _norm_product_terms(t)
    if prod: ss.product = prod

    if any(k in t for k in ("install", "installation", "set up", "setup", "deploy", "walk me through", "step by step")):
        ss.task = "installation"
    if any(k in t for k in ("design", "architecture", "size", "sizing", "capacity", "plan")):
        ss.task = "design"
    if any(k in t for k in ("troubleshoot", "error", "fail", "issue", "debug", "diagnose")):
        ss.task = "troubleshooting"
    if any(k in t for k in ("upgrade", "update", "patch")):
        ss.task = "upgrade"

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
        "End with ONE short follow-up only if useful.",
    ]
    if ss.product: parts.append(f"Product focus: {ss.product}.")
    if ss.task:    parts.append(f"Current task: {ss.task}.")
    if ss.depth:   parts.append(f"Depth: {ss.depth}.")
    return " [[ " + " ".join(parts) + " ]]"

def _to_chat_messages(hist, prompt=None) -> List[Dict[str, str]]:
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
# Reuse your existing model path so config stays centralized. :contentReference[oaicite:6]{index=6}
def call_llm(messages: List[Dict[str, str]]) -> str:
    from services.llm_service import generate_reply
    # Try chat-style kwargs first
    for kw in ("messages", "history", "context_messages", "conversation", "context"):
        try:
            return generate_reply(**{kw: messages})
        except TypeError:
            continue
    # Fallback: flatten as a prompt
    flat = "\n".join(f"{m.get('role','user')}: {m.get('content','')}" for m in messages if m.get("content"))
    return generate_reply(flat)

# -------------------------- Server-side intents -------------------------------

_ACCOUNT_PATTERNS = [
    re.compile(r"^\s*(?:do\s+you\s+know\s+)?(?:can\s+you\s+)?(?:what(?:'s| is)\s+)?(?:the\s+)?account\s+team(?:\s+(?:info(?:rmation)?|details)?)?\s+(?:for|at|on|about|regarding)\s+(.+?)\s*[?.!]*$", re.I),
    re.compile(r"^\s*who\s+(?:covers|owns)\s+(.+?)\s*[?.!]*$", re.I),
    re.compile(r"^\s*who\s+is\s+the\s+(?:pure\s+rep|account\s+owner)\s+(?:for|at|on|about|regarding)\s+(.+?)\s*[?.!]*$", re.I),
]

def _maybe_alias_org(q: str) -> List[str]:
    q = (q or "").strip()
    alts = [q]
    # simple aliases
    if q.lower().startswith("qvc"):
        alts.append("Qurate Retail Group")
        alts.append("QVC, Inc.")
    if "bank" in q.lower() and "chase" in q.lower():
        alts.append("JPMorgan Chase")
    return list(dict.fromkeys(alts))  # dedupe

def _render_team(obj: Dict[str, Any]) -> Optional[str]:
    if not obj: return None
    name  = obj.get("account_name") or obj.get("AccountName") or obj.get("name") or obj.get("customer")
    owner = obj.get("account_owner") or obj.get("AccountOwner") or obj.get("owner")
    rep   = obj.get("pure_rep") or obj.get("PureRep") or obj.get("rep")
    seg   = obj.get("type") or obj.get("Type") or obj.get("segment")
    if not any([name, owner, rep, seg]): return None
    parts = [f"**{name}**"]
    if owner: parts.append(f"Account Owner — {owner}")
    if rep:   parts.append(f"Pure Rep — {rep}")
    if seg:   parts.append(f"Type — {seg}")
    return "; ".join(parts)

def _search_account_team(q: str) -> Optional[str]:
    for candidate in _maybe_alias_org(q):
        results = search_accounts(candidate, limit=5) or []
        # pick first with any team info
        for r in results:
            rendered = _render_team(r)
            if rendered:
                return rendered
    return None

def _match_account_intent(text: str) -> Optional[str]:
    t = (text or "").strip()
    for rx in _ACCOUNT_PATTERNS:
        m = rx.match(t)
        if m and m.group(1):
            return m.group(1).strip()
    if "account team" in t.lower():
        m = re.search(r"(?:for|at|on|about|regarding)\s+(.+?)\s*[?.!]*$", t, re.I)
        if m: return m.group(1).strip()
    return None

def _is_email_history_intent(t: str) -> bool:
    t = (t or "").lower()
    return ("email" in t) and ("conversation" in t or "history" in t)

def _is_email_that_intent(t: str) -> bool:
    t = (t or "").lower()
    return ("email" in t) and ("that" in t or "it" in t)

# --------------------------------- Blueprint ----------------------------------

bp = Blueprint("conversation", __name__)

@bp.post("/api/chat")
def chat_orchestrated():
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

    # Normalize/track context
    infer_from_text(ss, text)

    # ---- Server-side intents (no model needed) ----
    # 1) Email conversation history to the signed-in user
    if _is_email_history_intent(text):
        try:
            hist = memory.get_recent_conversation(session["email"], limit=50) or []
        except Exception:
            hist = []
        if not hist:
            return jsonify({"ok": True, "reply": "I don’t have any history yet to email."})
        # Build a simple transcript
        lines = []
        for h in hist:
            role = (h.get("role") or "user").capitalize()
            msg  = h.get("message") or h.get("content") or ""
            lines.append(f"{role}: {msg}")
        plain = "\n".join(lines)
        html  = "<br>".join([f"<strong>{l.split(':',1)[0]}:</strong> {l.split(':',1)[1].strip()}" for l in lines if ':' in l])
        to = session["email"]
        ok = send_email(to=to, subject="Ask Chip — Conversation History", text=plain, html=f"<div>{html}</div>")
        reply = "I’ve emailed the conversation history to you." if ok else "I couldn’t send the conversation email just now."
        # log assistant reply
        try:
            memory.log_conversation(email=session["email"], role="assistant", message=reply)
        except Exception:
            pass
        ss.last_assistant = reply
        return jsonify({"ok": True, "reply": reply})

    # 2) Email the last assistant answer (“email that to me”)
    if _is_email_that_intent(text) and ss.last_assistant:
        to = session["email"]
        ok = send_email(to=to, subject="Ask Chip — Details you requested", text=ss.last_assistant, html=f"<div>{ss.last_assistant}</div>")
        reply = "Sent. Check your inbox."
        if not ok:
            reply = "I tried to email that, but it failed just now."
        try:
            memory.log_conversation(email=session["email"], role="assistant", message=reply)
        except Exception:
            pass
        ss.last_assistant = reply
        return jsonify({"ok": True, "reply": reply})

    # 3) Account team lookup
    acct_q = _match_account_intent(text)
    if acct_q:
        rendered = _search_account_team(acct_q)
        if rendered:
            reply = f"{rendered}. Want me to email that to you?"
        else:
            reply = f"I couldn’t find an account team for {acct_q}. Want me to try another spelling or related brand?"
        try:
            memory.log_conversation(email=session["email"], role="user", message=text)
            memory.log_conversation(email=session["email"], role="assistant", message=reply)
        except Exception:
            pass
        ss.last_assistant = reply
        return jsonify({"ok": True, "reply": reply, "state": {"product": ss.product, "task": ss.task, "depth": ss.depth}})

    # ---- Model path (Pure-first style + recent DB history) ----
    try:
        hist = memory.get_recent_conversation(session["email"], limit=8)
    except Exception:
        hist = []
    sys_msg = {"role": "system", "content": style_preamble(ss)}
    messages = [sys_msg] + _to_chat_messages(hist, prompt=text)

    reply = call_llm(messages) or ""
    # Persist turn and refresh memory from the answer
    try:
        memory.log_conversation(email=session["email"], role="user", message=text)
        memory.log_conversation(email=session["email"], role="assistant", message=reply)
    except Exception:
        pass

    infer_from_text(ss, reply)
    ss.last_assistant = reply
    return jsonify({"ok": True, "reply": reply, "state": {
        "product": ss.product, "task": ss.task, "depth": ss.depth
    }})
