from __future__ import annotations

import os, sys, json, base64, logging, time
from datetime import datetime
from typing import Dict, List
from flask import Flask, request, session, jsonify, render_template, url_for
from flask import Response
try:
    from flask_cors import CORS
except Exception:
    CORS = None

# --- App factory ---
app = Flask(__name__, static_folder="static", template_folder="templates")

# CORS (optional)
if CORS:
    origins = os.getenv("CORS_ORIGINS", "").strip() or "*"
    origins = [o.strip() for o in origins.split(",") if o.strip()]
    CORS(app, resources={r"/api/*": {"origins": origins}})

# Secret
app.secret_key = os.getenv("SECRET_KEY") or os.getenv("FLASK_SECRET") or "dev-secret-change-me"
app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=False)

# --- Safe blueprint registration ---
def _try_register(bp_import, name):
    try:
        bp = bp_import()
        app.register_blueprint(bp)
        return True
    except Exception as e:
        logging.warning("Blueprint %s not registered: %s", name, e)
        return False

# Prefer routes/* modules if present
try:
    from routes.voice import voice_bp as _voice_bp
    app.register_blueprint(_voice_bp)
except Exception as e:
    logging.warning("voice_bp not registered: %s", e)

try:
    from routes.chat import chat_bp as _chat_bp
    app.register_blueprint(_chat_bp)
except Exception as e:
    logging.warning("chat_bp not registered: %s", e)

try:
    from routes.conversation import conversation_bp as _conv_bp
    app.register_blueprint(_conv_bp)
except Exception as e:
    logging.warning("conversation_bp not registered: %s", e)

# Optional greet blueprint
try:
    from routes.greet import bp as greet_bp
    app.register_blueprint(greet_bp)
except Exception as e:
    logging.warning("greet bp not registered: %s", e)

# memory for login/profile
import memory

# DB init (non-fatal)
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

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
