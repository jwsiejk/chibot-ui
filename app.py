from __future__ import annotations

import os
import sys
import json
import base64
import logging
import inspect
from datetime import datetime
import time
from dataclasses import dataclass, field
from typing import Dict, List

from flask import (
    Flask, request, session, jsonify, render_template,
    url_for, Response, stream_with_context, current_app
)

# Word cap for replies (configurable via env)
WORD_CAP = int(os.getenv('CHIP_WORD_CAP', '30'))

# --- Optional blueprints (kept, do not error if missing) ---
try:
    from routes.voice import voice_bp
except Exception:
    voice_bp = None
try:
    from routes.chat import chat_bp
except Exception:
    chat_bp = None
try:
    from routes.conversation import conversation_bp
except Exception:
    conversation_bp = None

# Optional CORS: import lazily; initialize AFTER app is created
try:
    from flask_cors import CORS  # pip package name: Flask-Cors
except Exception:
    CORS = None

# --- Ensure we can import local packages ---
APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

# Optional support for a src/ layout
SRC_DIR = os.path.join(APP_DIR, "src")
if os.path.isdir(SRC_DIR) and SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# --- Load .env if available (no-op if missing) ---
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ------------------------------------------------------------------------------
# Normalize ElevenLabs env names BEFORE importing TTS (some libs read env on import)
# ------------------------------------------------------------------------------
def _first_env(*names):
    for n in names:
        v = os.getenv(n)
        if v and str(v).strip():
            return str(v).strip()
    return None

def _ensure_alias(target, *candidates):
    if not _first_env(target):
        val = _first_env(*candidates)
        if val is not None:
            os.environ[target] = val

# API keys (both ways)
_ensure_alias("ELEVEN_API_KEY", "ELEVENLABS_API_KEY")
_ensure_alias("ELEVENLABS_API_KEY", "ELEVEN_API_KEY")

# Voice ID (map your CHIP_VOICE_ID to common names)
_ensure_alias("ELEVEN_VOICE_ID", "CHIP_VOICE_ID", "ELEVENLABS_VOICE_ID")
_ensure_alias("ELEVENLABS_VOICE_ID", "ELEVEN_VOICE_ID", "CHIP_VOICE_ID")

# Model / Output format
_ensure_alias("ELEVEN_MODEL_ID", "ELEVENLABS_MODEL_ID")
_ensure_alias("ELEVENLABS_MODEL_ID", "ELEVEN_MODEL_ID")
_ensure_alias("ELEVEN_OUTPUT_FORMAT", "ELEVENLABS_OUTPUT_FORMAT")
_ensure_alias("ELEVENLABS_OUTPUT_FORMAT", "ELEVEN_OUTPUT_FORMAT")

def ELEVEN_API_KEY():    return _first_env("ELEVENLABS_API_KEY", "ELEVEN_API_KEY")
def ELEVEN_VOICE_ID():   return _first_env("CHIP_VOICE_ID", "ELEVEN_VOICE_ID", "ELEVENLABS_VOICE_ID")
def ELEVEN_MODEL_ID():   return _first_env("ELEVEN_MODEL_ID", "ELEVENLABS_MODEL_ID")
def ELEVEN_OUT_FORMAT(): return _first_env("ELEVEN_OUTPUT_FORMAT", "ELEVENLABS_OUTPUT_FORMAT") or "mp3_44100_128"

# --- Safe imports with fallbacks so the server can still boot ---
try:
    from services.llm_service import generate_reply, generate_response, generate_greeting, phrase_data, generate_followup, generate_nudge
except Exception as e:
    sys.stderr.write(f"[warning] llm_service import failed: {e}\n")
    def generate_reply(messages=None, **kwargs): return "Chip is running, but the LLM service is not available."
    def generate_response(user_text, history=None, **kwargs): return {"text": "Chip is running (fallback). Tell me your goal and product."}
    def generate_greeting(profile=None): return "Hey there—Chip here. What are we tackling?"
    def phrase_data(role, data, history=None, **kwargs): return "I can phrase this once services are available."
    def generate_followup(user_text, assistant_text, history=None, **kwargs): return {"text": ""}
    def generate_nudge(state_hint=None, history=None, **kwargs): return {"text": ""}

try:
    from services.tts_service import tts_bytes, tts_with_visemes
except Exception as e:
    sys.stderr.write(f"[warning] tts_service import failed: {e}\n")
    def tts_bytes(text, **kwargs): return b"", "TTS unavailable"
    def tts_with_visemes(text, **kwargs): return (b"", []), "TTS unavailable"

try:
    from services.email_service import send_email
except Exception as e:
    sys.stderr.write(f"[warning] email_service import failed: {e}\n")
    def send_email(*args, **kwargs): return False, "Email service unavailable"

try:
    from services.accounts_service import search_accounts
except Exception as e:
    sys.stderr.write(f"[warning] accounts_service import failed: {e}\n")
    def search_accounts(*args, **kwargs): return []

# Database helpers
import memory
import random
import re

# Flask setup
SECRET_KEY = os.getenv("SECRET_KEY") or os.getenv("FLASK_SECRET") or "dev-secret-change-me"
app = Flask(__name__, static_folder="static", template_folder="templates")

# Enable CORS only if package is available (avoid import crashes)
if CORS:
    origins = os.getenv("CORS_ORIGINS", "https://chibot-ui.onrender.com")
    origins = [o.strip() for o in origins.split(",") if o.strip()]
    CORS(app, resources={r"/api/*": {"origins": origins}})

# Register blueprints if available
if voice_bp: app.register_blueprint(voice_bp)
if chat_bp: app.register_blueprint(chat_bp)
if conversation_bp: app.register_blueprint(conversation_bp)

app.secret_key = SECRET_KEY
app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=False)

# Init DB (non-fatal if unavailable)
try:
    memory.init_db()
except Exception:
    pass

def current_user_email():
    return session.get("email")

@app.route("/")
def index():
    return render_template("index.html")

@app.get("/favicon.ico")
def favicon():
    return ("", 204)

# --- Health check for Render ---
@app.route("/api/health", methods=["GET"])
def api_health():
    def any_env(*names):
        return any(os.getenv(n, "").strip() for n in names)
    return jsonify({
        "openai_configured": bool(os.getenv("OPENAI_API_KEY", "").strip()),
        "eleven_configured": any_env("ELEVENLABS_API_KEY", "ELEVEN_API_KEY", "XI_API_KEY")
                              and any_env("ELEVENLABS_VOICE_ID", "ELEVEN_VOICE_ID", "CHIP_VOICE_ID"),
    })

@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return jsonify({"ok": False, "error": "Valid email required"}), 400
    session["email"] = email
    try:
        user = memory.get_user(email) or {}
        if not user:
            memory.save_user(email=email, name=None, title=None, region=None, profile=None)
    except Exception:
        pass
    return jsonify({"ok": True})

@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.pop("email", None)
    return jsonify({"ok": True})

@app.route("/api/me", methods=["GET"])
def api_me():
    email = current_user_email()
    if not email:
        return jsonify({"ok": True, "logged_in": False})
    user = memory.get_user(email) or {"email": email}
    profile_complete = bool(user.get("name"))
    return jsonify({"ok": True, "logged_in": True, "profile_complete": profile_complete, "user": user})

@app.route("/api/profile", methods=["GET", "POST"])
def api_profile():
    if request.method == "GET":
        if not current_user_email():
            return jsonify({"ok": False, "error": "Not authenticated"}), 401
        user = memory.get_user(current_user_email()) or {"email": current_user_email()}
        return jsonify({"ok": True, "user": user})
    if not current_user_email():
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    data = request.get_json(silent=True) or {}
    name = data.get("name")
    title = data.get("title")
    region = data.get("region")
    profile = data.get("profile")
    memory.save_user(email=current_user_email(), name=name, title=title, region=region, profile=profile)
    user = memory.get_user(current_user_email())
    return jsonify({"ok": True, "user": user})

def chip_dynamic_greet(user):
    try:
        return generate_greeting(user)
    except Exception:
        return ""

# ----------------------------- TTS helpers ------------------------------------
def _parse_tts_payload(payload, default_fmt):
    """
    Accept many shapes and return:
      { "audio": <base64 string>, "visemes": [...], "format": <fmt> }
    Return None if unrecognized.
    """
    fmt = default_fmt or "mp3_44100_128"

    if isinstance(payload, (bytes, bytearray)):
        return {"audio": base64.b64encode(payload).decode("ascii"), "visemes": [], "format": fmt}

    if isinstance(payload, tuple) and len(payload) == 2:
        a, b = payload
        if isinstance(a, (bytes, bytearray)) and (b is None or isinstance(b, (list, tuple))):
            return {"audio": base64.b64encode(a).decode("ascii"), "visemes": list(b or []), "format": fmt}
        return _parse_tts_payload(a, fmt)

    if isinstance(payload, dict):
        result = {"visemes": list(payload.get("visemes") or []), "format": payload.get("format") or fmt}
        if "audio_b64" in payload and isinstance(payload["audio_b64"], str):
            result["audio"] = payload["audio_b64"]
            return result
        if "audio" in payload:
            if isinstance(payload["audio"], (bytes, bytearray)):
                result["audio"] = base64.b64encode(payload["audio"]).decode("ascii")
            elif isinstance(payload["audio"], str):
                result["audio"] = payload["audio"]
            else:
                return None
            return result
        if "audio_bytes" in payload and isinstance(payload["audio_bytes"], (bytes, bytearray)):
            result["audio"] = base64.b64encode(payload["audio_bytes"]).decode("ascii")
            return result
    return None

def cap_30_words(s: str) -> str:
    words = (s or "").split()
    return " ".join(words[:30])

# ------------------------ LLM wrapper (signature-flex) ------------------------
def _to_chat_messages(hist, prompt=None):
    """
    Convert a heterogeneous history into OpenAI Chat Completions format:
      [{"role": "...", "content": "..."}]
    """
    msgs = []
    if isinstance(hist, (list, tuple)):
        for item in hist:
            role = "user"
            content = None
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

def _generate_reply_flex(prompt: str, profile: dict, hist):
    """
    Call services.llm_service.generate_reply regardless of its current signature.
    """
    try:
        sig = inspect.signature(generate_reply)
        params = sig.parameters

        hist_msgs = _to_chat_messages(hist)
        msgs_with_prompt = _to_chat_messages(hist, prompt=prompt)

        kwargs = {}
        if 'profile' in params: kwargs['profile'] = profile
        elif 'user' in params:  kwargs['user'] = profile
        elif 'persona' in params: kwargs['persona'] = profile

        if 'context_messages' in params: kwargs['context_messages'] = hist_msgs
        elif 'history' in params:       kwargs['history'] = hist_msgs
        elif 'messages' in params:      kwargs['messages'] = hist_msgs
        elif 'context' in params:       kwargs['context'] = hist_msgs
        elif 'conversation' in params:  kwargs['conversation'] = hist_msgs

        try:
            return generate_reply(prompt, **kwargs)
        except TypeError:
            pass

        for name in ('messages', 'history', 'context_messages', 'conversation', 'context'):
            if name in params:
                try:
                    return generate_reply(**{name: msgs_with_prompt})
                except TypeError:
                    continue

        try:
            return generate_reply(prompt)
        except Exception:
            logging.exception("generate_reply(prompt) failed last attempt")
            return "I'm having trouble generating a reply right now."
    except Exception:
        logging.exception("generate_reply invocation failed")
        return "I'm having trouble generating a reply right now."

# ----------------------------------------------------------------------
# Conversational chat endpoint (unified)
# ----------------------------------------------------------------------
@dataclass
class _Msg:
    role: str
    content: str
    t: float = field(default_factory=time.time)

@dataclass
class _SessionState:
    history: List[_Msg] = field(default_factory=list)   # optional local echo
    product: str = ""    # FlashBlade | FlashArray | Portworx
    task: str = ""       # installation | design | troubleshooting | upgrade
    depth: str = ""      # high | deep
    account: str = ""
    goal: str = ""
    constraints: str = ""
    decisions: str = ""
    next_step: str = ""
    running_summary: str = ""
    turns: int = 0
    last_summary_at: float = 0.0
    last_seen: float = field(default_factory=time.time)

_SESS: Dict[str, _SessionState] = {}
_TTL = 60 * 60  # 1 hour

def _sid_key():
    return current_user_email() or request.remote_addr

def _gc_sessions():
    now = time.time()
    for k in list(_SESS.keys()):
        if now - _SESS[k].last_seen > _TTL:
            _SESS.pop(k, None)

def _infer_from_text(ss: _SessionState, text: str):
    t = (text or "").lower()
    if any(k in t for k in ("flashblade", "flash blade", "fb-s3", "s3 on flashblade", "fb")):
        ss.product = "FlashBlade"
    if any(k in t for k in ("flasharray", "flash array", "fa", "purity//fa", "purity/fa")):
        ss.product = "FlashArray"
    if "portworx" in t:
        ss.product = "Portworx"

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

def _cap_words(text: str, cap: int = WORD_CAP) -> str:
    try:
        words = (text or "").split()
        if len(words) <= cap:
            return text or ""
        out = " ".join(words[:cap]).rstrip(",;:—-")
        if not out.endswith(('.', '!', '?')):
            out += "."
        return out
    except Exception:
        return (text or "")

# --------------------------- Conversation State Helpers ---------------------------
def _state_json(ss: "_SessionState") -> dict:
    return {
        "product": ss.product,
        "account": ss.account,
        "goal": ss.goal,
        "constraints": ss.constraints,
        "decisions": ss.decisions,
        "next_step": ss.next_step
    }

def _inject_state_and_summary(ss: "_SessionState", hist: List[dict]) -> List[dict]:
    """Prepend system messages for pinned state and running summary before raw history."""
    prefix = []
    try:
        st = json.dumps(_state_json(ss), ensure_ascii=False)
        prefix.append({"role": "system", "content": f"SESSION_STATE: {st}"})
    except Exception:
        prefix.append({"role": "system", "content": "SESSION_STATE: {}"})
    if ss.running_summary:
        prefix.append({"role": "system", "content": f"RUNNING_SUMMARY: {ss.running_summary}"})
    return prefix + (hist or [])

def _llm_update_state(ss: "_SessionState", user_text: str, assistant_text: str, hist: List[dict]):
    """Ask the LLM to refresh pinned state fields from the latest turn. No canned text emitted to user."""
    try:
        from services.llm_service import generate_reply
    except Exception:
        return
    prior = _state_json(ss)

    prompt = f"""Update the session state JSON with keys: product, account, goal, constraints, decisions, next_step.
Use only brief, plain phrases. Keep values if unchanged.
Return ONLY a JSON object, nothing else.

PRIOR_STATE: {json.dumps(prior, ensure_ascii=False)}
USER: {user_text}
ASSISTANT: {assistant_text}"""

    try:
        updated = generate_reply(messages=[{"role": "user", "content": prompt}], max_tokens=160, temperature=0.2)
        if not updated:
            return
        data = json.loads(updated)
        ss.product     = str(data.get("product")     or ss.product     or "")
        ss.account     = str(data.get("account")     or ss.account     or "")
        ss.goal        = str(data.get("goal")        or ss.goal        or "")
        ss.constraints = str(data.get("constraints") or ss.constraints or "")
        ss.decisions   = str(data.get("decisions")   or ss.decisions   or "")
        ss.next_step   = str(data.get("next_step")   or ss.next_step   or "")
    except Exception:
        pass

def _llm_update_summary(ss: "_SessionState", email: str):
    """Refresh running_summary every few turns: 5–7 short spoken lines; no bullets/numbers."""
    try:
        from services.llm_service import generate_reply
    except Exception:
        return
    try:
        hist = (memory.get_recent_conversation(email, limit=16)
                if hasattr(memory, "get_recent_conversation") else [])
    except Exception:
        hist = []

    # Build a compact textual transcript excerpt
    lines = []
    for m in hist[-12:]:
        role = (m.get("role") or "").lower()
        msg = (m.get("message") or m.get("text") or m.get("content") or "")[:300]
        if role in ("user", "assistant"):
            lines.append(f"{role.upper()}: {msg}")
    convo = "\n".join(lines)

    prompt = f"""Summarize the conversation so far into 5–7 short spoken lines (no bullets or numbers).
Capture decisions, key numbers, blockers, and the current goal. Write in compact, natural speech.

PRIOR_SUMMARY: {ss.running_summary or ""}
CONVERSATION:
{convo}"""

    try:
        new_sum = generate_reply(messages=[{"role": "user", "content": prompt}], max_tokens=220, temperature=0.3)
        if new_sum:
            ss.running_summary = new_sum.strip()
            ss.last_summary_at = time.time()
            ss.turns = 0
    except Exception:
        pass
# ------------------------- /Conversation State Helpers ---------------------------

@app.route("/api/chat", methods=["POST"])
def api_chat():
    if not current_user_email():
        return jsonify({"ok": False, "error": "Not authenticated"}), 401

    data = request.get_json(silent=True) or {}
    text = (data.get("text") or data.get("prompt") or data.get("message") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "Prompt required"}), 400

    key = _sid_key()
    ss = _SESS.get(key) or _SessionState()
    _SESS[key] = ss
    ss.last_seen = time.time()
    _gc_sessions()

    _infer_from_text(ss, text)
    try:
        memory.log_conversation(email=current_user_email(), role="user", message=text)
    except Exception:
        pass

    try:
        hist = memory.get_recent_conversation(current_user_email(), limit=10)
    except Exception:
        hist = []

    # Inject pinned state + running summary; persona-only (no style preamble injection)
    aug_hist = _inject_state_and_summary(ss, hist or [])
    resp = generate_response(user_text=text, history=aug_hist)
    reply = (resp.get("text") if isinstance(resp, dict) else str(resp or "")).strip()
    reply = _cap_words(reply, WORD_CAP)

    # Update state + roll summary occasionally
    try:
        _llm_update_state(ss, text, reply, hist or [])
        ss.turns = (ss.turns or 0) + 1
        if ss.turns >= 3:
            _llm_update_summary(ss, current_user_email())
    except Exception:
        pass

    try:
        memory.log_conversation(email=current_user_email(), role="assistant", message=reply)
    except Exception:
        pass
    return jsonify({"ok": True, "reply": reply})

@app.route("/api/voice/tts", methods=["POST"])
def api_tts():
    if not current_user_email():
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    data = request.get_json(silent=True) or {}
    text = cap_30_words((data.get("text") or "").strip())
    if not text:
        return jsonify({"ok": False, "error": "Text required"}), 400
    res = tts_bytes(text)
    audio = None
    err = None
    if isinstance(res, tuple) and len(res) == 2 and isinstance(res[1], (str, type(None))):
        audio, err = res
    elif isinstance(res, (bytes, bytearray)):
        audio = res
    elif isinstance(res, dict):
        if "audio_bytes" in res and isinstance(res["audio_bytes"], (bytes, bytearray)):
            audio = res["audio_bytes"]
        elif "audio" in res and isinstance(res["audio"], str):
            try:
                audio = base64.b64decode(res["audio"])
            except Exception:
                audio = None
        err = res.get("error")
    if not audio:
        return jsonify({"ok": False, "error": err or "TTS unavailable"}), 503
    return Response(audio, mimetype="audio/mpeg")

@app.route("/api/voice/tts_with_visemes", methods=["POST"])
def api_tts_with_visemes():
    if not current_user_email():
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    data = request.get_json(silent=True) or {}
    text = cap_30_words((data.get("text") or "").strip())
    if not text:
        return jsonify({"ok": False, "error": "Text required"}), 400
    res = tts_with_visemes(text)
    payload = _parse_tts_payload(res, ELEVEN_OUT_FORMAT())
    if not payload:
        return jsonify({"ok": False, "error": "Unexpected TTS payload"}), 500
    return jsonify({"ok": True, **payload})

@app.route("/api/email/send", methods=["POST"])
def api_email_send():
    if not current_user_email():
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    data = request.get_json(silent=True) or {}
    to_addr = (data.get("to") or "").strip()
    subject = (data.get("subject") or "").strip()
    body = (data.get("body") or "").strip()
    html = data.get("html")
    if not to_addr:
        return jsonify({"ok": False, "error": "Recipient required"}), 400
    ok, err = send_email(to_addr, subject, body, html)
    if not ok:
        return jsonify({"ok": False, "error": err or "Send failed"}), 502
    try:
        memory.log_conversation(email=current_user_email(), role="assistant", message=f"[Email sent to {to_addr}] {subject}")
    except Exception:
        pass
    return jsonify({"ok": True})

@app.route("/api/accounts/search", methods=["GET"])
def api_accounts_search():
    if not current_user_email():
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    q = (request.args.get("q") or "").strip()
    results = search_accounts(q, limit=25) if q else []
    return jsonify({"ok": True, "results": results})

# --- BEGIN: Orchestrator fallback aliases (app-level, additive) ---
def _orchestrator_ok_payload(resp):
    if isinstance(resp, dict):
        text = resp.get("text") or resp.get("reply") or resp.get("message") or ""
    elif isinstance(resp, str):
        text = resp
    else:
        text = str(resp)
    text = (text or "").strip()
    return {"ok": True, "text": text, "reply": text, "message": text}

def _orchestrate_now(text, history):
    try:
        return _orchestrator_ok_payload(_generate_reply_flex(
            text, memory.get_user(current_user_email()) or {}, history
    )), 200
    except Exception:
        return _orchestrator_ok_payload(
            "I hit a snag but I’m ready to continue. Want a quick overview or step-by-step?"
        ), 200

@app.route("/orchestrator", methods=["GET", "POST", "OPTIONS"])
@app.route("/orchestrate", methods=["GET", "POST", "OPTIONS"])
@app.route("/conversation", methods=["GET", "POST", "OPTIONS"])
@app.route("/api/v1/orchestrator", methods=["GET", "POST", "OPTIONS"])
@app.route("/v1/orchestrator", methods=["GET", "POST", "OPTIONS"])
def app_orchestrator_fallback():
    if request.method == "OPTIONS":
        return ("", 204)
    data = request.get_json(silent=True) or {}
    text = (data.get("message") or data.get("text") or data.get("prompt") or request.args.get("q") or "").strip()
    history = data.get("history") or data.get("messages") or []
    if not isinstance(history, (list, tuple)):
        history = []
    if not text:
        return jsonify(_orchestrator_ok_payload("Tell me what you want to tackle and I’ll jump in.")), 200
    payload, status = _orchestrate_now(text, history)
    return jsonify(payload), status
# --- END: Orchestrator fallback aliases ---

# --- BEGIN: server-side cancel + SSE chat stream ---
_CANCEL = {}  # { email: timestamp }

def _mark_cancel(email: str):
    _CANCEL[email] = time.time()

def _was_cancelled(email: str, since: float) -> bool:
    return _CANCEL.get(email, 0) > since

@app.post("/api/interrupt")
def api_interrupt():
    if not current_user_email():
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    _mark_cancel(current_user_email())
    return jsonify({"ok": True})

def _maybe_tool(text: str):
    """
    Minimal demo intents (uses your send_email/search_accounts):
      - email <address> subject: ... body: ...
      - accounts search for <query>
    """
    t = (text or "").lower()
    if t.startswith("email ") or " email " in t:
        to = subject = body = None
        m = re.search(r'email\s+([^\s]+)', t)
        if m: to = m.group(1)
        ms = re.search(r'subject:\s*([^;]+)', t)
        if ms: subject = ms.group(1).strip()
        mb = re.search(r'body:\s*(.+)', t)
        if mb: body = mb.group(1).strip()
        return {"name": "email.send", "args": {"to": to, "subject": subject, "body": body}}
    if "account" in t or "search" in t:
        q = None
        m = re.search(r'for\s+(.+)', t)
        if m: q = m.group(1).strip()
        return {"name": "accounts.search", "args": {"q": q or text, "limit": 25}}
    return None

@app.get("/api/chat/stream")
def api_chat_stream():
    if not current_user_email():
        return Response("unauthorized", 401)
    user = current_user_email()
    started = time.time()
    text = (request.args.get("q") or "").strip()
    if not text:
        return Response("missing q", 400)

    def _events():
        yield 'event: start\ndata: {}\n\n'

        tool = _maybe_tool(text)
        if tool:
            yield 'event: tool_call\ndata: ' + json.dumps(tool) + '\n\n'
            try:
                if tool["name"] == "email.send":
                    ok, err = send_email(
                        tool["args"]["to"],
                        tool["args"].get("subject") or "(no subject)",
                        tool["args"].get("body") or "",
                        None
                    )
                    data = {"ok": bool(ok), "error": err}
                elif tool["name"] == "accounts.search":
                    res = search_accounts(tool["args"]["q"], limit=tool["args"].get("limit", 25))
                    data = {"ok": True, "results": res}
                else:
                    data = {"ok": False, "error": "unknown tool"}
            except Exception as e:
                data = {"ok": False, "error": str(e)}
            yield 'event: tool_result\ndata: ' + json.dumps(data) + '\n\n'
            if _was_cancelled(user, started):
                yield 'event: interrupted\ndata: {}\n\n'
                return

        try:
            hist = memory.get_recent_conversation(user, limit=10)
        except Exception:
            hist = []
        resp = generate_response(user_text=text, history=hist)
        reply = (resp.get("text") if isinstance(resp, dict) else str(resp or "")).strip()

        if not reply:
            yield 'event: done\ndata: {}\n\n'
            return

        import re as _re
        chunks = _re.split(r'(?<=[.!?])\s+', reply)
        for c in chunks:
            if not c:
                continue
            if _was_cancelled(user, started):
                yield 'event: interrupted\ndata: {}\n\n'
                return
            yield 'event: token\ndata: ' + json.dumps({"delta": c + " "}) + '\n\n'
            time.sleep(0.05)

        yield 'event: done\ndata: {}\n\n'

    return Response(stream_with_context(_events()), mimetype="text/event-stream")
# --- END: server-side cancel + SSE chat stream ---

# Orchestrator health alias (kept)
@app.route("/api/orchestrator/health", methods=["GET"])
def api_orchestrator_health():
    return api_health()

# --- Persona-driven phrasing endpoint ---
@app.route("/api/phrase", methods=["POST"])
def api_phrase():
    if not current_user_email():
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    try:
        data = request.get_json(silent=True) or {}
        role = (data.get("role") or "").strip() or "info"
        payload = data.get("data") or {}
        hist = memory.get_recent_conversation(current_user_email(), limit=6) or []
        text = phrase_data(role, payload, history=hist)
        return jsonify({"ok": True, "text": text})
    except Exception as e:
        current_app.logger.exception("api_phrase failed")
        return jsonify({"ok": False, "error": "phrase_failed", "detail": str(e)}), 500

# --- Dynamic follow-up and nudge endpoints ---
@app.route("/api/followup", methods=["POST"])
def api_followup():
    if not current_user_email():
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    data = request.get_json(silent=True) or {}
    user_text = (data.get("user_text") or "").strip()
    assistant_text = (data.get("assistant_text") or "").strip()
    try:
        hist = memory.get_recent_conversation(current_user_email(), limit=10)
    except Exception:
        hist = []
    try:
        resp = generate_followup(user_text=user_text, assistant_text=assistant_text, history=hist)
        return jsonify({"ok": True, "text": resp.get("text","")})
    except Exception as e:
        current_app.logger.exception("api_followup failed")
        return jsonify({"ok": False, "error": "followup_failed", "detail": str(e)}), 500

@app.route("/api/nudge", methods=["POST"])
def api_nudge():
    if not current_user_email():
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    data = request.get_json(silent=True) or {}
    state = data.get("state") or {}
    try:
        hist = memory.get_recent_conversation(current_user_email(), limit=6)
    except Exception:
        hist = []
    try:
        resp = generate_nudge(state_hint=state, history=hist)
        return jsonify({"ok": True, "text": resp.get("text","")})
    except Exception as e:
        current_app.logger.exception("api_nudge failed")
        return jsonify({"ok": False, "error": "nudge_failed", "detail": str(e)}), 500

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
