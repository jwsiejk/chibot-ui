import os
import sys
import json
import base64
import logging
from datetime import datetime

from flask import Flask, request, session, jsonify, render_template, url_for, Response

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
# Normalize environment variables so ElevenLabs works with your existing names.
# You use CHIP_VOICE_ID and ELEVENLABS_API_KEY; some libs expect ELEVEN_VOICE_ID /
# ELEVEN_API_KEY. Mirror values BEFORE importing any TTS code that may read env.
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
    from services.llm_service import generate_reply
except Exception as e:
    sys.stderr.write(f"[warning] llm_service import failed: {e}\n")
    def generate_reply(messages, **kwargs):
        return "Chip is running, but the LLM service is not available."

# NOTE: Do NOT register blueprints here; 'app' is not defined yet and
# your active /api/greet lives in this file.
# (The old code attempted app.register_blueprint before creating app.)

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

# Flask setup
SECRET_KEY = os.getenv("SECRET_KEY") or os.getenv("FLASK_SECRET") or "dev-secret-change-me"
app = Flask(__name__, static_folder="static", template_folder="templates")
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
    # Avoid 404 noise if favicon is not present
    return ("", 204)

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
    # POST
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

import random

def chip_dynamic_greet(user):
    name = (user.get("name") or "there")
    role = (user.get("title") or "").strip()
    region = (user.get("region") or "").strip()
    options = [
        f"Morning, {name}. Chip here—what are we tackling today?",
        f"Hey {name}, Chip checking in. Want me to walk through something or sanity‑check a plan?",
        f"Howdy {name}! Ready to get practical—where should we start?",
        f"Hi {name}. Chip at your service. Curious what you want to sort out first.",
        f"Alright {name}, Chip’s on deck. What’s the job today?"
    ]
    if region:
        options.append(f"Hey {name} in {region}—what should we dive into?")
    if role:
        options.append(f"Hi {name} ({role}). Want me to get hands‑on, or keep it high‑level?")
    return random.choice(options)

# ----------------------------- TTS helpers ------------------------------------

def _parse_tts_payload(payload, default_fmt):
    """
    Accept many shapes and return:
      { "audio": <base64 string>, "visemes": [...], "format": <fmt> }
    Return None if unrecognized.
    """
    fmt = default_fmt or "mp3_44100_128"

    # bytes -> base64
    if isinstance(payload, (bytes, bytearray)):
        return {"audio": base64.b64encode(payload).decode("ascii"), "visemes": [], "format": fmt}

    # (bytes, list) tuple OR (payload, err)
    if isinstance(payload, tuple) and len(payload) == 2:
        a, b = payload
        # (bytes, visemes)
        if isinstance(a, (bytes, bytearray)) and (b is None or isinstance(b, (list, tuple))):
            return {"audio": base64.b64encode(a).decode("ascii"), "visemes": list(b or []), "format": fmt}
        # Assume (payload, err) — recurse on first item
        return _parse_tts_payload(a, fmt)

    # dict variants
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

# ------------------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------------------

@app.route("/api/greet", methods=["GET"])
def api_greet():
    email = current_user_email()
    if not email:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    user = memory.get_user(email) or {}
    text = chip_dynamic_greet(user)

    # Try to return audio + visemes. Do NOT pass keywords; service reads env.
    try:
        res = tts_with_visemes(text)
        payload = _parse_tts_payload(res, ELEVEN_OUT_FORMAT())
        if payload:
            payload.update({"ok": True, "text": text})
            return jsonify(payload)
    except Exception:
        pass

    # Fallback to static file if exists
    audio_rel = "chip/audio/greeting-static.mp3"
    audio_fs = os.path.join(app.static_folder, "chip", "audio", "greeting-static.mp3")
    audio_url = url_for("static", filename=audio_rel) if os.path.exists(audio_fs) else None
    return jsonify({"ok": True, "text": text, "audioUrl": audio_url})

@app.route("/api/chat", methods=["POST"])
def api_chat():
    if not current_user_email():
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    data = request.get_json(silent=True) or {}
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"ok": False, "error": "Prompt required"}), 400

    user = memory.get_user(current_user_email()) or {}
    try:
        hist = memory.get_recent_conversation(current_user_email(), limit=8)
    except Exception:
        # DB may be missing 'role' after a reset; fall back to empty history.
        hist = []

    reply = generate_reply(prompt, profile=user, context_messages=hist)
    reply = cap_30_words(reply or "")

    try:
        memory.log_conversation(email=current_user_email(), role="user", message=prompt)
        memory.log_conversation(email=current_user_email(), role="assistant", message=reply)
    except Exception:
        pass

    return jsonify({"ok": True, "reply": reply})

@app.route("/api/tts", methods=["POST"])
def api_tts():
    if not current_user_email():
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    data = request.get_json(silent=True) or {}
    text = cap_30_words((data.get("text") or "").strip())
    if not text:
        return jsonify({"ok": False, "error": "Text required"}), 400

    res = tts_bytes(text)  # do NOT pass keywords; service reads env

    # Accept (audio, err) or bytes or dict
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

@app.route("/api/tts_with_visemes", methods=["POST"])
def api_tts_with_visemes():
    if not current_user_email():
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    data = request.get_json(silent=True) or {}
    text = cap_30_words((data.get("text") or "").strip())
    if not text:
        return jsonify({"ok": False, "error": "Text required"}), 400

    res = tts_with_visemes(text)  # do NOT pass keywords; service reads env

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

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
