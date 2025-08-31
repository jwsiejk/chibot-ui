from __future__ import annotations

import os
import json
import time
import datetime as _dt
import logging
from flask import Flask, jsonify, render_template, request, session
from pathlib import Path
from werkzeug.exceptions import HTTPException

# Optional services
import memory
from utils.call_log import call_log

def _bool_env(name: str, default: str = "0") -> bool:
    return (os.getenv(name, default) or "").strip() in ("1","true","True","yes","on")

def _feature_flags() -> dict:
    return {
        "AUDIO": _bool_env("FEATURE_AUDIO", "0"),
        "HISTORY": _bool_env("FEATURE_HISTORY", "0"),
        "ADMIN_UI": _bool_env("FEATURE_ADMIN_UI", "0"),
        "CONVERSATION": _bool_env("FEATURE_CONVERSATION", "0"),  # should remain off; conversation endpoints are removed
        "TOOLS": _bool_env("FEATURE_TOOLS", "0"),
        "EMAIL": _bool_env("FEATURE_EMAIL", "1"),
        "ACCOUNTS": _bool_env("FEATURE_ACCOUNTS", "0"),
        "SUMMARY": _bool_env("FEATURE_SUMMARY", "0"),
    }

def create_app():
    # Point Flask at repo-root templates/ and static/
    ROOT = Path(__file__).resolve().parents[1]  # /opt/render/project/src
    app = Flask(
        __name__,
        template_folder=str(ROOT / "templates"),
        static_folder=str(ROOT / "static"),
        static_url_path="/static",
    )
    app.config["JSON_SORT_KEYS"] = False
    app.logger.setLevel(logging.INFO)

    # Health (simple)
    @app.get("/api/health")
    def health():
        return jsonify({"ok": True, "time": _dt.datetime.utcnow().isoformat()+"Z"})

    # Me (session-based)
    @app.get("/api/me")
    def me():
        email = (session.get("user", {}) or {}).get("email") or session.get("email")
        profile = session.get("profile") or {}
        authenticated = bool(email)
        profile_complete = bool(profile.get("name") and profile.get("email"))
        return jsonify({
            "ok": True,
            "authenticated": authenticated,
            "profileComplete": profile_complete,
            "email": email,
            "profile": profile
        })

    # Public features (consumed by UI to hide disabled controls)
    @app.get("/api/features")
    def features():
        f = _feature_flags()
        return jsonify({"ok": True, "features": f})

    # Index
    @app.route("/", methods=["GET"])
    def index():
        return render_template("index.html")

    # If your Render start command is "gunicorn app.legacy_app:app", keep this:
app = create_app()
# If your command is "gunicorn app.legacy_app:create_app()", remove or ignore the line above.

    # ---- Register blueprints (canonical only) ----
    # Voice API (optional)
    try:
        if _feature_flags()["AUDIO"]:
            from routes.voice import voice_bp as _voice_bp
            if "voice_bp" not in app.blueprints:
                app.register_blueprint(_voice_bp, url_prefix="/api/voice")
    except Exception as e:
        app.logger.warning("Voice blueprint unavailable: %s", e)

    # Profile
    try:
        from routes.profile import profile_bp as _profile_bp
        if "profile_bp" not in app.blueprints:
            app.register_blueprint(_profile_bp, url_prefix="/api")
    except Exception as e:
        app.logger.warning("Profile blueprint unavailable: %s", e)

    # Chat (canonical /api/chat)
    try:
        from routes.chat import chat_bp as _chat_bp
        if "chat_bp" not in app.blueprints:
            app.register_blueprint(_chat_bp)
    except Exception as e:
        app.logger.warning("Chat blueprint unavailable: %s", e)

    # Greet
    try:
        from routes.greet import bp as _greet_bp
        if "greet" not in app.blueprints:
            app.register_blueprint(_greet_bp)
    except Exception as e:
        app.logger.warning("Greet blueprint unavailable: %s", e)

    # Accounts (optional)
    try:
        if _feature_flags()["ACCOUNTS"]:
            from routes.accounts import accounts_bp as _accounts_bp
            if "accounts_bp" not in app.blueprints:
                app.register_blueprint(_accounts_bp)
    except Exception as e:
        app.logger.warning("Accounts blueprint unavailable: %s", e)

    # Email (on by default)
    try:
        if _feature_flags()["EMAIL"]:
            from routes.email_api import email_bp as _email_bp
            if "email_bp" not in app.blueprints:
                app.register_blueprint(_email_bp)
    except Exception as e:
        app.logger.warning("Email blueprint unavailable: %s", e)

    # Tools / diagnostics pages (optional)
    try:
        if _feature_flags()["TOOLS"]:
            from routes.tools import tools_bp as _tools_bp
            if "tools_bp" not in app.blueprints:
                app.register_blueprint(_tools_bp)  # /askchip-diagnostics.html, /admin-log.html
    except Exception as e:
        app.logger.warning("Tools blueprint unavailable: %s", e)

    # Admin UI and log stream (optional)
    try:
        if _feature_flags()["ADMIN_UI"]:
            _wire_admin_log_routes(app)
    except Exception as e:
        app.logger.warning("Admin wiring unavailable: %s", e)

    # ---------- Error handling (force JSON) ----------
    @app.errorhandler(HTTPException)
    def _http_error(e: HTTPException):
        payload = {"ok": False, "status": e.code, "error": e.name, "path": request.path}
        return jsonify(payload), e.code

    @app.errorhandler(Exception)
    def _uncaught(e: Exception):
        app.logger.exception("Unhandled server error")
        return jsonify({"ok": False, "status": 500, "error": "Internal Server Error", "detail": str(e), "path": request.path}), 500

    return app

# ---- helper that wires admin log routes & hooks onto an app instance ----
def _wire_admin_log_routes(app: Flask):
    from flask import jsonify, render_template, request, session, Response, stream_with_context, g
    import json, time, datetime as _dt
    from utils.call_log import call_log

    def _is_admin_email(email: str | None) -> bool:
        allowed = os.getenv("ADMIN_EMAILS", "")
        if not allowed:
            return False
        emails = [s.strip().lower() for s in allowed.split(",") if s.strip()]
        return (email or "").lower() in emails

    @app.route("/admin/call-log", methods=["GET"])
    def admin_call_log_page():
        # Require admin
        email = (session.get("user", {}) or {}).get("email") or session.get("email")
        if not _is_admin_email(email):
            return jsonify({"ok": False, "error": "forbidden"}), 403
        return render_template("admin_call_log.html")

    @app.route("/admin/stream", methods=["GET"])
    def admin_log_stream():
        email = (session.get("user", {}) or {}).get("email") or session.get("email")
        if not _is_admin_email(email):
            return jsonify({"ok": False, "error": "forbidden"}), 403
        def _gen():
            # Keep simple; the underlying call_log is already batching
            last_ix = 0
            while True:
                items = call_log.dump_since(last_ix)
                if items:
                    last_ix = items[-1]["id"]
                    yield f"data: {json.dumps(items)}\n\n"
                time.sleep(0.5)
        return Response(stream_with_context(_gen()), mimetype="text/event-stream")
