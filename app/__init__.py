
# app/__init__.py
import os
from flask import Flask, Blueprint, render_template, request, session, current_app

from .api_v1 import create_v1_blueprint
from .api_v1.health import bp as health_bp
from .middleware.csrf import csrf_before_request, make_csrf_route, ensure_csrf_headers
from .middleware.rate_limit import register_before_request as rate_limit_register
from .config import load_settings

# ---------- Core blueprint (UI shells / docs) ----------
core_bp = Blueprint("core", __name__)

_SETTINGS = load_settings()

@core_bp.get("/")
def home():
    return render_template("index.html")

@core_bp.get("/diagnostics")
def diagnostics():
    return render_template("diagnostics.html")

@core_bp.get("/favicon.ico")
def favicon():
    # Serve from /static/favicon.ico
    from flask import current_app, send_file
    import os as _os
    return send_file(_os.path.join(current_app.static_folder, "favicon.ico"), mimetype="image/x-icon")

@core_bp.get("/logs-ui")
def logs_ui_redirect():
    # Convenience redirect for the admin log UI
    from flask import redirect
    return redirect("/api/v1/admin/logs-ui", code=302)

@core_bp.get("/admin")
def admin_console():
    # Admin-only HTML console
    from .utils.admin import is_admin_email
    from .security_state import get_user
    from flask import abort, render_template as _rt, session as _sess, request as _req
    email = (_sess.get("user") or {}).get("email") or _sess.get("email") or _req.headers.get("X-User-Email") or (get_user() or "")
    if not is_admin_email((email or "").strip().lower()):
        abort(403)
    return _rt("admin_console.html")
def create_app():
    app = Flask(
        __name__,
        static_folder="../static",
        static_url_path="/static",
        template_folder="../templates",
    )
    app.config["JSON_SORT_KEYS"] = False
    if os.environ.get("CI_FAST"):
        app.config["TESTING"] = True

    app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

    # v1-only API + core + health
    app.register_blueprint(create_v1_blueprint(), url_prefix="/api/v1")
    app.register_blueprint(core_bp)
    app.register_blueprint(health_bp)

    # Ensure CSRF header/cookie attached to responses
    app.after_request(ensure_csrf_headers)

    # Middleware
    app.before_request(csrf_before_request)
    rate_limit_register(app)

    make_csrf_route(app)

    def asset_version(rel_path: str) -> str:
        try:
            full_path = os.path.join(current_app.static_folder, rel_path)
            return str(int(os.path.getmtime(full_path)))
        except (OSError, TypeError, ValueError):
            return "0"

    @app.context_processor
    def _inject_asset_version():
        return {"asset_version": asset_version}

    @app.context_processor
    def _inject_askchip_config():
        enabled = True
        try:
            enabled = bool(getattr(_SETTINGS, "advanced_logging_enabled", True))
        except Exception:
            enabled = True
        auth_config = {}
        auto_login_email = (os.environ.get("ASKCHIP_AUTO_LOGIN_EMAIL") or "").strip()
        if auto_login_email:
            auth_config["autoLoginEmail"] = auto_login_email
        vad_config = {}
        try:
            vad_config = {
                "baseThresholdDb": float(getattr(_SETTINGS, "vad_base_threshold_db", 10.0)),
                "exitThresholdDb": float(getattr(_SETTINGS, "vad_exit_threshold_db", 6.0)),
                "ttsBoostDb": float(getattr(_SETTINGS, "vad_tts_boost_db", 6.0)),
                "minSpeechMs": int(getattr(_SETTINGS, "vad_min_speech_ms", 360)),
            }
        except Exception:
            vad_config = {}

        feature_flags = {
            "feature_manual_barge_in": True,
            "barge_in_mode_manual": False,
            "auto_commit_when_ready": True,
        }
        try:
            from .services import admin_settings as _admin_settings  # inline import to avoid cycles

            settings = _admin_settings.get_settings()
        except Exception:
            settings = {}
        try:
            from .db import db as _db

            db_cfg = _db.get_config()
        except Exception:
            db_cfg = {}

        def _resolve_flag(name: str, default: bool) -> bool:
            if isinstance(settings, dict) and name in settings:
                return bool(settings.get(name))
            if isinstance(db_cfg, dict) and name in db_cfg:
                return bool(db_cfg.get(name))
            return default

        feature_flags["feature_manual_barge_in"] = _resolve_flag(
            "feature_manual_barge_in", True
        )
        feature_flags["barge_in_mode_manual"] = _resolve_flag(
            "barge_in_mode_manual", False
        )
        feature_flags["auto_commit_when_ready"] = _resolve_flag(
            "auto_commit_when_ready", True
        )
        return {
            "askchip_config": {
                "logging": {"enabled": enabled},
                "auth": auth_config,
                "vad": vad_config,
                "features": feature_flags,
                "feature_manual_barge_in": feature_flags["feature_manual_barge_in"],
                "barge_in_mode_manual": feature_flags["barge_in_mode_manual"],
                "auto_commit_when_ready": feature_flags["auto_commit_when_ready"],
            }
        }

    @app.after_request
    def maybe_allow_cors(resp):
        allow = os.environ.get("CORS_ALLOW_ORIGINS", "")
        if allow:
            origins = [o.strip() for o in allow.split(",") if o.strip()]
            ori = request.headers.get("Origin", "")
            if ori in origins:
                resp.headers["Access-Control-Allow-Origin"] = ori
                resp.headers["Vary"] = "Origin"
                resp.headers["Access-Control-Allow-Credentials"] = "true"
                resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-CSRF-Token"
                resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return resp

    @app.route("/<path:_>", methods=["OPTIONS"])
    def _cors_preflight(_):
        return ("", 204)

    @app.before_request
    def _auth_gate():
        p = request.path or "/"
        allow = (
            p.startswith("/api/") or
            p.startswith("/ws") or
            p.startswith("/static") or
            p.startswith("/favicon") or
            p.startswith("/docs/") or
            p.startswith("/admin") or
            p == "/" or
            p == "/login" or
            p.startswith("/profile")
        )
        if allow:
            return
        if not (session.get("user") or {}).get("email"):
            from flask import redirect, url_for
            return redirect(url_for("core.home"))

    return app
