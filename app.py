# app.py — Flask app factory, config, JSON error handling, and blueprint registration
import os
import logging
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

# --- DB helpers (expects your working memory.py per 2025-08-08 note) ---
# Provides: init_db(), get_user(email), save_user(profile_dict), log_conversation(...)
from memory import init_db, get_user, save_user, log_conversation  # will raise if missing

# --- Blueprints ---
from chat_routes import chat_bp
from voice_routes import voice_bp


def create_app() -> Flask:
    app = Flask(__name__, static_folder="static", static_url_path="/static")

    # Trust proxy headers (Render/NGINX)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

    # Basic config
    app.config["ENV"] = os.getenv("FLASK_ENV", "production")
    app.config["JSON_SORT_KEYS"] = False
    app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25 MB uploads
    app.config["CORS_ORIGINS"] = os.getenv("CORS_ORIGINS", "*")

    # CORS
    CORS(
        app,
        resources={r"/*": {"origins": app.config["CORS_ORIGINS"]}},
        supports_credentials=True,
    )

    # Logging
    logging.basicConfig(level=logging.INFO)
    app.logger.setLevel(logging.INFO)

    # Ensure folders exist
    Path(app.static_folder, "audio").mkdir(parents=True, exist_ok=True)
    Path(app.static_folder, "uploads").mkdir(parents=True, exist_ok=True)

    # Initialize DB
    init_db()

    # --- Request logging (concise) ---
    @app.before_request
    def _log_request():
        app.logger.info(f'{request.remote_addr} {request.method} {request.path}')

    # --- JSON error handlers ---
    @app.errorhandler(404)
    def _not_found(e):
        return jsonify({"error": "not_found", "path": request.path}), 404

    @app.errorhandler(413)
    def _too_large(e):
        return jsonify({"error": "payload_too_large"}), 413

    @app.errorhandler(Exception)
    def _server_error(e):
        app.logger.exception("Unhandled error")
        return jsonify({"error": "server_error", "detail": str(e)}), 500

    # --- Lightweight API root & health ---
    @app.get("/")
    def index():
        return jsonify({"app": "Ask Chip", "message": "API root"})

    @app.get("/healthz")
    def healthz():
        return jsonify(
            {
                "ok": True,
                "time": datetime.utcnow().isoformat() + "Z",
                "env": app.config["ENV"],
            }
        )

    # --- Auth/profile helpers used by the UI ---
    @app.get("/api/me")
    def api_me():
        """
        Returns the current user's profile (dev-friendly: accepts ?email=...).
        Frontend may also pass X-User-Email header.
        """
        email = (request.args.get("email") or request.headers.get("X-User-Email") or "").strip()
        if not email:
            return jsonify(
                {"email": None, "isAdmin": False, "name": None, "profileComplete": False, "title": None}
            )
        user = get_user(email) or {}
        profile_complete = bool(user.get("name") and user.get("title"))
        return jsonify(
            {
                "email": email,
                "isAdmin": bool(user.get("is_admin")),
                "name": user.get("name"),
                "profileComplete": profile_complete,
                "title": user.get("title"),
            }
        )

    @app.get("/api/profile")
    def api_profile_get():
        email = (request.args.get("email") or request.headers.get("X-User-Email") or "").strip()
        if not email:
            return jsonify({"error": "email required"}), 400
        user = get_user(email) or {"email": email}
        return jsonify(user)

    @app.post("/api/profile")
    def api_profile_post():
        data = request.get_json(force=True, silent=True) or {}
        email = (data.get("email") or request.headers.get("X-User-Email") or "").strip()
        if not email:
            return jsonify({"error": "email required"}), 400
        payload = {
            "email": email,
            "name": (data.get("name") or "").strip() or None,
            "title": (data.get("title") or "").strip() or None,
            "is_admin": bool(data.get("isAdmin")),
        }
        save_user(payload)
        return jsonify({"ok": True, "profile": payload})

    # --- Register feature blueprints ---
    # NOTE: /greet lives in voice_routes.py and is registered here.
    app.register_blueprint(chat_bp)
    app.register_blueprint(voice_bp)

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
