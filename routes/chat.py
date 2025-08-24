from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List
import time, json, re
from flask import Blueprint, request, jsonify, session, current_app
import memory
from services.llm_service import generate_response, phrase_data, generate_followup, generate_nudge

chat_bp = Blueprint('chat', __name__, url_prefix='/api')

WORD_CAP = int(__import__('os').getenv('CHIP_WORD_CAP','40'))

@dataclass
class _Msg:
    role: str
    content: str
    t: float = field(default_factory=time.time)

@dataclass
class SessionState:
    history: List[_Msg] = field(default_factory=list)
    product: str = ""
    task: str = ""
    depth: str = ""
    account: str = ""
    goal: str = ""
    constraints: str = ""
    decisions: str = ""
    next_step: str = ""
    running_summary: str = ""
    turns: int = 0
    last_summary_at: float = 0.0
    last_seen: float = field(default_factory=time.time)

_SESS: Dict[str, SessionState] = {}
_TTL_SECONDS = 60 * 60

def _sid() -> str:
    return session.get("email") or request.remote_addr or "anon"

def _gc():
    now = time.time()
    for k in list(_SESS.keys()):
        if now - _SESS[k].last_seen > _TTL_SECONDS:
            _SESS.pop(k, None)

def _infer(ss: SessionState, text: str) -> Dict[str,bool]:
    t = (text or "").lower()
    guesses = {}
    if any(k in t for k in ("flashblade","flash blade","fb")): ss.product, guesses["FlashBlade"] = "FlashBlade", True
    if any(k in t for k in ("flasharray","flash array","fa","purity//fa","purity/fa")): ss.product, guesses["FlashArray"] = "FlashArray", True
    if "portworx" in t: ss.product, guesses["Portworx"] = "Portworx", True

    if any(k in t for k in ("install","installation","setup","set up","deploy","walk me through","step by step")): ss.task = "installation"
    if any(k in t for k in ("design","architecture","size","sizing","capacity","plan")): ss.task = "design"
    if any(k in t for k in ("troubleshoot","error","fail","issue","debug","diagnose")): ss.task = "troubleshooting"
    if any(k in t for k in ("upgrade","update","patch")): ss.task = "upgrade"

    if "high level" in t or "overview" in t or "summary" in t: ss.depth = "high"
    if "step by step" in t or "walk" in t or "detailed" in t or "deep" in t: ss.depth = "deep"

    return guesses

def _state_json(ss: SessionState) -> dict:
    return {
        "product": ss.product, "account": ss.account, "goal": ss.goal,
        "constraints": ss.constraints, "decisions": ss.decisions, "next_step": ss.next_step
    }

def _inject_state(ss: SessionState, hist: List[dict]) -> List[dict]:
    prefix = []
    try:
        st = json.dumps(_state_json(ss), ensure_ascii=False)
        prefix.append({"role":"system","content": f"SESSION_STATE: {st}"})
    except Exception:
        prefix.append({"role":"system","content":"SESSION_STATE: {}"})
    if ss.running_summary:
        prefix.append({"role":"system","content": f"RUNNING_SUMMARY: {ss.running_summary}"})
    return prefix + (hist or [])

re_bullet = re.compile(r'^\s*(?:\d+[\.\)]|[-*•])\s+')
def _anti_list(text: str) -> str:
    if not text: return text
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    bullets = [l for l in lines if re_bullet.match(l)]
    if len(bullets) >= max(2, len(lines)//2):
        parts = [re_bullet.sub("", l).strip(" :-") for l in lines]
        flow = []
        for i,p in enumerate(parts):
            if i==0: flow.append(f"First, {p}")
            elif i==1: flow.append(f"Next, {p}")
            elif i==len(parts)-1: flow.append(f"Finally, {p}")
            else: flow.append(f"Then, {p}")
        return " ".join(flow)
    cleaned = [re_bullet.sub("", l).strip(" :-") for l in lines]
    return " ".join(cleaned)

def _limit(text: str, cap: int = WORD_CAP) -> str:
    words = (text or "").split()
    if len(words) <= cap:
        return text or ""
    out = " ".join(words[:cap]).rstrip(",;:—-")
    if not out.endswith(('.', '!', '?')):
        out += "."
    return out

def _maybe_ask_for_product(guesses: Dict[str,bool], text: str) -> str|None:
    # If ambiguous (e.g., "flashlight" or both FlashArray/FlashBlade hinted), ask a human-like clarifier.
    lower = (text or "").lower()
    if "flashlight" in lower or (guesses.get("FlashArray") and guesses.get("FlashBlade")):
        return "Did you mean FlashArray or FlashBlade? I can tailor the answer once you pick."
    # If we guessed Portworx plus something else, also clarify.
    if guesses and len([k for k,v in guesses.items() if v]) > 1:
        opts = ", ".join([k for k,v in guesses.items() if v])
        return f"Looks like it could be {opts}. Which one are you asking about?"
    return None

@chat_bp.route('/chat', methods=['POST'])
def chat():
    if not session.get("email"):
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    data = request.get_json(force=True, silent=True) or {}
    user_text = (data.get('message') or data.get('text') or '').strip()
    if not user_text:
        return jsonify({"ok": False, "error": "Prompt required"}), 400

    key = _sid()
    ss = _SESS.get(key) or SessionState()
    _SESS[key] = ss
    ss.last_seen = time.time()
    _gc()
    guesses = _infer(ss, user_text)

    try:
        memory.log_conversation(email=session.get("email"), role="user", message=user_text)
    except Exception:
        pass

    # EARLY CLARIFIER if ambiguous
    clar = _maybe_ask_for_product(guesses, user_text)
    if clar:
        reply = clar
        try:
            memory.log_conversation(email=session.get("email"), role="assistant", message=reply)
        except Exception:
            pass
        return jsonify({"ok": True, "reply": reply})

    try:
        hist = memory.get_recent_conversation(session.get("email"), limit=10) or []
    except Exception:
        hist = []

    aug_hist = _inject_state(ss, hist)
    resp = generate_response(user_text=user_text, history=aug_hist)
    reply = (resp.get("text") if isinstance(resp, dict) else str(resp or "")).strip()
    reply = _anti_list(_limit(reply))

    # lightweight running summary update
    try:
        ss.turns = (ss.turns or 0) + 1
        if ss.turns % 3 == 0 and hist:
            last = " ".join((m.get("message") or m.get("text") or m.get("content") or "") for m in hist[-4:])
            ss.running_summary = (ss.running_summary + " " + last).strip()[:1200]
    except Exception:
        pass

    try:
        memory.log_conversation(email=session.get("email"), role="assistant", message=reply)
    except Exception:
        pass
    return jsonify({"ok": True, "reply": reply})

@chat_bp.route('/phrase', methods=['POST'])
def phrase():
    if not session.get("email"):
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    data = request.get_json(force=True, silent=True) or {}
    role = (data.get("role") or "info").strip()
    payload = data.get("data") or {}
    try:
        hist = memory.get_recent_conversation(session.get("email"), limit=6) or []
    except Exception:
        hist = []
    try:
        text = phrase_data(role, payload, history=hist)
        return jsonify({"ok": True, "text": text})
    except Exception as e:
        current_app.logger.exception("api_phrase failed")
        return jsonify({"ok": False, "error": "phrase_failed", "detail": str(e)}), 500

@chat_bp.route('/followup', methods=['POST'])
def followup():
    if not session.get("email"):
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    data = request.get_json(force=True, silent=True) or {}
    user_text = (data.get("user_text") or "").strip()
    assistant_text = (data.get("assistant_text") or "").strip()
    try:
        hist = memory.get_recent_conversation(session.get("email"), limit=10) or []
    except Exception:
        hist = []
    try:
        resp = generate_followup(user_text=user_text, assistant_text=assistant_text, history=hist)
        return jsonify({"ok": True, "text": resp.get("text","")})
    except Exception as e:
        current_app.logger.exception("api_followup failed")
        return jsonify({"ok": False, "error": "followup_failed", "detail": str(e)}), 500

@chat_bp.route('/nudge', methods=['POST'])
def nudge():
    if not session.get("email"):
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    data = request.get_json(force=True, silent=True) or {}
    state = data.get("state") or {}
    try:
        hist = memory.get_recent_conversation(session.get("email"), limit=6) or []
    except Exception:
        hist = []
    try:
        resp = generate_nudge(state_hint=state, history=hist)
        return jsonify({"ok": True, "text": resp.get("text","")})
    except Exception as e:
        current_app.logger.exception("api_nudge failed")
        return jsonify({"ok": False, "error": "nudge_failed", "detail": str(e)}), 500
