# routes/chat.py — guarded /api/chat with ambiguity gate, anti-echo, anti-list
import re, json, time, difflib
from flask import Blueprint, request, jsonify, session
try:
    import memory
except Exception:
    memory = None

try:
    # your real LLM
    from services.llm_service import generate_response
except Exception as e:
    # safe fallback that never echoes
    def generate_response(user_text: str, history=None, **kwargs):
        return {"text": "I’m up, but the model isn’t responding right now. Want a quick overview or step-by-step?"}

chat_bp = Blueprint("chat", __name__)

# --- helpers --------------------------------------------------------------
def _norm(s: str) -> str:
    return re.sub(r'\s+', ' ', (s or '').strip().lower())

_PX_PAT = re.compile(r'\bport\s*worx\b|\bport\s*works\b|\bportworx\b|\bpx\b', re.I)
_FA_PAT = re.compile(r'\bflash\s*array\b|\bflasharray\b|\bfa\b', re.I)
_FB_PAT = re.compile(r'\bflash\s*blade\b|\bflashblade\b|\bfb\b', re.I)
_FLASHLIGHT = re.compile(r'\bflash\s*light\b|\bflashlight\b', re.I)

def _maybe_clarify(user: str) -> str | None:
    t = _norm(user)
    # typical ambiguities
    if _FLASHLIGHT.search(t):
        return "Do you mean FlashArray or FlashBlade? I can tailor it either way."
    if _PX_PAT.search(t) and not any(p.search(t) for p in (_FA_PAT, _FB_PAT)):
        # spelled weird (“port works”) -> confirm product
        return "Sounds like Portworx—do you want install tips, design pointers, or troubleshooting?"
    # single tokens that are pure nouns; ask for direction
    if t in {"fa","fb","flasharray","flash array","flashblade","flash blade","portworx","port worx","px"}:
        return "Which area should we hit—installation, design, troubleshooting, or a quick briefing?"
    # one or two short words -> clarify
    if len(t.split()) <= 2 and len(t) <= 14:
        return "Got it—what’s the goal? (install, design, troubleshoot, or overview)"
    return None

def _anti_list(txt: str) -> str:
    if not txt: return ""
    # 1) replace bullets / numbers at sentence starts
    s = re.sub(r'(^|\n)\s*([•\-\*]|\d+\.)\s*', lambda m: (m.group(1) + ""), txt)
    # 2) compress repeated newlines
    s = re.sub(r'\n{2,}', '\n', s).strip()
    # 3) simple numbered sequence -> spoken transitions
    s = re.sub(r'\b1\)\s*', 'First, ', s)
    s = re.sub(r'\b2\)\s*', 'Next, ', s)
    s = re.sub(r'\b3\)\s*', 'Then, ', s)
    s = re.sub(r'\b4\)\s*', 'Finally, ', s)
    s = re.sub(r'\b\d+\.\s*', '', s)
    return s

def _is_echo(user: str, reply: str) -> bool:
    u = _norm(user)
    r = _norm(reply)
    if not u or not r: return False
    if u == r: return True
    # treat minor punctuation/case changes as echo
    ratio = difflib.SequenceMatcher(a=u, b=r).ratio()
    return ratio >= 0.92

# --- route ---------------------------------------------------------------
@chat_bp.post("/api/chat")
def api_chat():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or data.get("prompt") or data.get("message") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "Prompt required"}), 400

    # early clarification
    clar = _maybe_clarify(text)
    if clar:
        if memory and session.get("email"):
            try: memory.log_conversation(email=session["email"], role="user", message=text)
            except Exception: pass
            try: memory.log_conversation(email=session["email"], role="assistant", message=clar)
            except Exception: pass
        return jsonify({"ok": True, "reply": clar})

    # gather short history for context
    hist = []
    if memory and session.get("email"):
        try: hist = memory.get_recent_conversation(session["email"], limit=10)
        except Exception: hist = []

    # call model
    resp = generate_response(user_text=text, history=hist)
    reply = (resp.get("text") if isinstance(resp, dict) else str(resp or "")).strip()

    # post-process
    if _is_echo(text, reply):
        reply = _maybe_clarify(text) or "I can help—do you want a quick overview, step‑by‑step, or troubleshooting?"

    reply = _anti_list(reply)

    if memory and session.get("email"):
        try: memory.log_conversation(email=session["email"], role="user", message=text)
        except Exception: pass
        try: memory.log_conversation(email=session["email"], role="assistant", message=reply)
        except Exception: pass

    return jsonify({"ok": True, "reply": reply})
