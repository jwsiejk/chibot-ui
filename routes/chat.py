
# routes/chat.py — guarded /api/chat with intent, memory, and style (no lists, anti‑echo)
import os, re, json, difflib
from flask import Blueprint, request, jsonify, session

# Optional memory store
try:
    import memory  # provides logs, preferences, summaries
except Exception:  # pragma: no cover
    memory = None  # safe fallback

# LLM entrypoints
try:
    from services.llm_service import generate_smart_response, summarize_session
except Exception:  # pragma: no cover
    generate_smart_response = None
    summarize_session = None

try:
    from services.llm_service import generate_response  # compat fallback
except Exception:  # pragma: no cover
    generate_response = None

try:
    from services.llm_service import generate_reply  # last-resort
except Exception:  # pragma: no cover
    generate_reply = None

try:
    from services.intents import classify_intent
except Exception:  # pragma: no cover
    classify_intent = None

chat_bp = Blueprint("chat", __name__)
WORD_CAP = int(os.getenv("CHIP_WORD_CAP", "30"))

# ----------------------- helpers -----------------------
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())

_PX_PAT  = re.compile(r"\bport\s*worx\b|\bport\s*works\b|\bportworx\b|\bpx\b", re.I)
_FA_PAT  = re.compile(r"\bflash\s*array\b|\bflasharray\b|\bfa\b", re.I)
_FB_PAT  = re.compile(r"\bflash\s*blade\b|\bflashblade\b|\bfb\b", re.I)
_LIGHT   = re.compile(r"\bflash\s*light\b|\bflashlight\b", re.I)

def _maybe_clarify(user_text: str) -> str | None:
    t = _norm(user_text)
    if not t:
        return "Tell me the goal and product—install, design, troubleshoot, or a quick overview?"
    # classic ambiguity
    if _LIGHT.search(t):
        return "Do you mean FlashArray or FlashBlade? I can tailor it either way."
    # single-product words with no task
    if t in {"fa","fb","px","flasharray","flash array","flashblade","flash blade","portworx","port worx"}:
        return "Got it—do you want installation help, design sizing, troubleshooting, or a brief overview?"
    # 'port works' etc.
    if _PX_PAT.search(t) and not any(p.search(t) for p in (_FA_PAT, _FB_PAT)):
        return "Sounds like Portworx—do you want install tips, design pointers, or troubleshooting?"
    # very short utterances → ask for direction
    if len(t.split()) <= 2 and len(t) <= 14:
        return "What should we hit—install, design, troubleshoot, or a quick briefing?"
    return None

def _anti_list(txt: str) -> str:
    """Strip list bullets and map simple 1)/2)/3) into spoken transitions."""
    if not txt:
        return ""
    s = txt.replace("\r\n", "\n")
    # remove bullets/number prefixes at line starts
    s = re.sub(r"(^|\n)\s*([•\-*]|\d+[\.)])\s*", lambda m: (m.group(1) if m.group(1) else ""), s)
    # map numeric enumerations inside sentences
    s = re.sub(r"\b1\)\s*", "First, ", s)
    s = re.sub(r"\b2\)\s*", "Next, ", s)
    s = re.sub(r"\b3\)\s*", "Then, ", s)
    s = re.sub(r"\b4\)\s*", "Finally, ", s)
    # generic "1. " patterns
    s = re.sub(r"\b\d+\.\s*", "", s)
    # collapse extra newlines/spaces
    s = re.sub(r"\n{2,}", "\n", s)
    s = re.sub(r"\s{3,}", "  ", s)
    return s.strip()

def _is_echo(user: str, reply: str) -> bool:
    """Detect near-echo replies (common on very short prompts)."""
    u = _norm(user)
    r = _norm(reply)
    if not u or not r:
        return False
    if u == r:
        return True
    try:
        return difflib.SequenceMatcher(None, u, r).ratio() >= 0.95
    except Exception:
        return False

def _get_hist(email: str):
    if not (memory and email):
        return []
    try:
        h = memory.get_recent_conversation(email, limit=10)
        return h or []
    except Exception:
        return []

def _log(email: str, role: str, text: str):
    if not (memory and email and text):
        return
    try:
        memory.log_conversation(email=email, role=role, message=text)
    except Exception:
        pass

# ----------------------- route -----------------------
@chat_bp.post("/api/chat")
def api_chat():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or data.get("prompt") or data.get("message") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "Prompt required"}), 400

    debug = str(request.args.get("debug") or "").lower() in {"1", "true", "yes"}
    meta = {"path": "chat_guarded", "clarified": False, "anti_echo": False, "anti_list": False, "smart": False}

    email = None
    try:
        email = session.get("email")
    except Exception:
        email = None

    # 1) Early clarification for ambiguous/short inputs
    clar = _maybe_clarify(text)
    if clar:
        meta["clarified"] = True
        _log(email, "user", text)
        _log(email, "assistant", clar)
        payload = {"ok": True, "reply": clar}
        if debug: payload["meta"] = meta
        return jsonify(payload)

    # 2) Smart context (intent + memory) if available
    hist = _get_hist(email)
    prefs = {}
    summary = None
    notes = []
    try:
        if memory and email:
            prefs = memory.get_preferences(email) or {}
            summary = memory.get_session_summary(email)
            notes = memory.recall_notes(email, text, k=3) or []
    except Exception:
        prefs, summary, notes = {}, None, []
    # channel hint
    channel = str(data.get("channel") or prefs.get("channel") or "web").lower()

    intent = None
    if callable(classify_intent):
        try:
            intent = classify_intent(text)
        except Exception:
            intent = None

    reply = ""
    if callable(generate_smart_response):
        try:
            out = generate_smart_response(
                user_text=text, history=hist, intent=intent,
                session_summary=summary, memories=notes, prefs=prefs,
                channel=channel, word_cap=WORD_CAP
            )
            reply = str((out or {}).get("text") or "")
            meta["smart"] = True
        except Exception:
            reply = ""

    # 3) Fall back to legacy responders if needed
    if not reply and callable(generate_response):
        try:
            out = generate_response(user_text=text, history=hist)
            if isinstance(out, dict):
                reply = str(out.get("text") or out.get("reply") or out.get("message") or "").strip()
            else:
                reply = str(out or "").strip()
        except Exception:
            reply = ""
    if not reply and callable(generate_reply):
        try:
            out = generate_reply(text)
            if isinstance(out, dict):
                reply = str(out.get("text") or out.get("reply") or out.get("message") or "").strip()
            else:
                reply = str(out or "").strip()
        except Exception:
            reply = ""

    if not reply:
        reply = "I can help—do you want a quick overview, step‑by‑step, or troubleshooting?"

    # 4) Anti-echo (if model mirrored the user)
    if _is_echo(text, reply):
        meta["anti_echo"] = True
        reply = "I can help—do you want a quick overview, step‑by‑step, or troubleshooting?"

    # 5) Anti-list (spoken style)
    dressed = _anti_list(reply)
    if dressed != reply:
        meta["anti_list"] = True
        reply = dressed

    # 6) Log + lightweight summary update
    _log(email, "user", text)
    _log(email, "assistant", reply)
    try:
        if memory and email and callable(summarize_session):
            msgs = hist + [{"role":"user","message":text},{"role":"assistant","message":reply}]
            summ = summarize_session(msgs)
            if summ:
                memory.set_session_summary(email, summ)
    except Exception:
        pass

    payload = {"ok": True, "reply": reply}
    if debug: payload["meta"] = meta
    return jsonify(payload)
