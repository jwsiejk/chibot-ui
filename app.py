import os
import sys
import json
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

# --- Load .env if available (does nothing if not present) ---
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# --- Safe imports with fallbacks so the server can still boot ---
try:
    from services.llm_service import generate_reply
except Exception as e:
    sys.stderr.write(f"[warning] llm_service import failed: {e}\n")
    def generate_reply(messages, **kwargs):
        return "Chip is running, but the LLM service is not available."

try:
    from routes.greet import bp as greet_bp
    app.register_blueprint(greet_bp)
except Exception as e:
    import sys
    sys.stderr.write(f"[warning] greet blueprint failed: {e}\n")

try:
    from services.tts_service import tts_bytes, tts_with_visemes
except Exception as e:
    sys.stderr.write(f"[warning] tts_service import failed: {e}\n")
    def tts_bytes(text, **kwargs): return b""
    def tts_with_visemes(text, **kwargs): return b"", []

try:
    from services.email_service import send_email
except Exception as e:
    sys.stderr.write(f"[warning] email_service import failed: {e}\n")
    def send_email(*args, **kwargs): return False

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
    # Light personalization
    if region:
        options.append(f"Hey {name} in {region}—what should we dive into?")
    if role:
        options.append(f"Hi {name} ({role}). Want me to get hands‑on, or keep it high‑level?")
    return random.choice(options)

@app.route("/api/greet", methods=["GET"])

def api_greet():
    email = current_user_email()
    if not email:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    user = memory.get_user(email) or {}
    text = chip_dynamic_greet(user)
    # Try to return TTS + visemes for smooth greeting
    try:
        data, err = tts_with_visemes(text)
        if not err and data:
            return jsonify({"ok": True, "text": text, "audio": data.get("audio"), "visemes": data.get("visemes"), "relative": data.get("relative", True)})
    except Exception:
        pass
    # Fallback to static file if exists
    audio_rel = "chip/audio/greeting-static.mp3"
    audio_fs = os.path.join(app.static_folder, "chip", "audio", "greeting-static.mp3")
    audio_url = url_for("static", filename=audio_rel) if os.path.exists(audio_fs) else None
    return jsonify({"ok": True, "text": text, "audioUrl": audio_url})

def cap_30_words(s: str) -> str:
    words = (s or "").split()
    return " ".join(words[:30])

@app.route("/api/chat", methods=["POST"])
def api_chat():
    if not current_user_email():
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    data = request.get_json(silent=True) or {}
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"ok": False, "error": "Prompt required"}), 400

    user = memory.get_user(current_user_email()) or {}
    hist = memory.get_recent_conversation(current_user_email(), limit=8)
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
    audio, err = tts_bytes(text)
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
    payload, err = tts_with_visemes(text)
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
