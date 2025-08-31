from __future__ import annotations

import os
import logging
import importlib
from datetime import datetime as _dt
from pathlib import Path
from typing import Any, Optional, Dict

from flask import Flask, jsonify, render_template, request, session
from werkzeug.exceptions import HTTPException

# Optional internal deps
try:
    import memory  # type: ignore
except Exception:  # pragma: no cover
    class _MemStub:
        def get_user(self, email: str) -> Dict[str, Any]:
            return {}
    memory = _MemStub()  # type: ignore

try:
    from utils.call_log import call_log  # type: ignore
except Exception:  # pragma: no cover
    class _CallLogStub:
        def add(self, name: str, status: str, **kw: Any) -> None:
            pass
    call_log = _CallLogStub()  # type: ignore


def _bool_env(name: str, default: bool = False) -> bool:
    truthy = {"1", "true", "yes", "on"}
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in truthy


def _str_env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return default if v is None else v


def create_app() -> Flask:
    """Application factory with repo-root templates/static and canonical API."""
    ROOT = Path(__file__).resolve().parents[1]

    app = Flask(
        __name__,
        template_folder=str(ROOT / "templates"),
        static_folder=str(ROOT / "static"),
        static_url_path="/static",
    )

    # Basic config
    app.config.setdefault("JSON_SORT_KEYS", False)
    app.config.setdefault("JSON_AS_ASCII", False)
    app.config.setdefault("TEMPLATES_AUTO_RELOAD", True)
    app.config.setdefault("SECRET_KEY", _str_env("SECRET_KEY", "change-me"))

    if not app.debug and not app.testing:
        logging.basicConfig(level=logging.INFO)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _register(module_path: str, attr_name: Optional[str] = None, url_prefix: Optional[str] = None) -> None:
        """Import module and register its Blueprint attribute safely."""
        try:
            mod = importlib.import_module(module_path)
        except Exception as e:  # pragma: no cover
            app.logger.info("Skipping %s: import failed: %s", module_path, e)
            return

        names = [attr_name] if attr_name else ["bp", "blueprint", "api_bp", "chat_bp", "profile_bp", "tools_bp", "admin_bp", "email_bp", "voice_bp", "auth_bp"]
        bp = None
        for nm in names:
            if nm and hasattr(mod, nm):
                bp = getattr(mod, nm)
                break
        if bp is None:
            for nm in dir(mod):
                obj = getattr(mod, nm)
                if getattr(obj, "register", None) and getattr(obj, "name", None) and getattr(obj, "url_prefix", None) is not None:
                    bp = obj
                    break
        if bp is None:
            app.logger.info("Skipping %s: no blueprint found", module_path)
            return
        try:
            if bp.name in app.blueprints:
                app.logger.info("Blueprint %s already registered; skipping", bp.name)
            else:
                app.register_blueprint(bp, url_prefix=url_prefix)
                app.logger.info("Registered blueprint from %s as %s (url_prefix=%r)", module_path, getattr(bp, "name", "?"), url_prefix)
        except Exception as e:  # pragma: no cover
            app.logger.warning("Failed registering blueprint %s: %s", module_path, e)

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------
    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/healthz")
    def healthz():
        return jsonify(ok=True, time=_dt.utcnow().isoformat() + "Z")

    @app.get("/favicon.ico")
    def favicon():
        # Return empty favicon to avoid noisy 404s if no file is present
        return ("", 204, {"Cache-Control": "max-age=86400"})

    @app.get("/api/features")
    def api_features():
        data = {
            "AUDIO": _bool_env("FEATURE_AUDIO", True),
            "HISTORY": _bool_env("FEATURE_HISTORY", False),
            "ADMIN_UI": _bool_env("FEATURE_ADMIN_UI", False),
            "TOOLS": _bool_env("FEATURE_TOOLS", False),
            "EMAIL": _bool_env("FEATURE_EMAIL", True),
            "ACCOUNTS": _bool_env("FEATURE_ACCOUNTS", False),
        }
        return jsonify(ok=True, features=data)

    @app.get("/api/me")
    def api_me():
        email = session.get("email") or session.get("user_email")
        profile: Dict[str, Any] = {}
        try:
            if email:
                profile = memory.get_user(email) or {}
        except Exception:
            profile = {}
        profile_complete = bool(profile.get("name") or profile.get("fullname"))
        return jsonify({
            "ok": True,
            "authenticated": bool(email),
            "profileComplete": profile_complete,
            "first_time": not profile_complete,
            "email": email,
            "profile": profile,
            # legacy keys for older UIs
            "logged_in": bool(email),
            "profile_complete": profile_complete,
        })

    # ------------------------------------------------------------------
    # Blueprints (canonical only — no conversation/orchestrator)
    # ------------------------------------------------------------------
    # Tools / diagnostics (optional)
    if _bool_env("FEATURE_TOOLS"):
        _register("routes.tools", "tools_bp")

    # Admin UI (optional)
    if _bool_env("FEATURE_ADMIN_UI"):
        _register("routes.admin", "admin_bp")

    # Profile (mount under /api)
    try:
        from routes.profile import profile_bp as _profile_bp  # type: ignore
        if "profile_bp" not in app.blueprints:
            app.register_blueprint(_profile_bp, url_prefix="/api")
        else:
            app.logger.info("profile_bp already registered; skipping")
    except Exception as e:  # pragma: no cover
        app.logger.info("Profile blueprint not available: %s", e)

    # Auth (session-based login/logout)
    _register("routes.auth", "auth_bp", url_prefix=None)  # blueprint defines url_prefix="/api"

    # Greet and Chat
    _register("routes.greet", "bp")
    _register("routes.chat", "chat_bp")

    # Email API (optional)
    if _bool_env("FEATURE_EMAIL", True):
        _register("routes.email_api", None)

    # Accounts search (optional)
    if _bool_env("FEATURE_ACCOUNTS", False):
        _register("routes.accounts", None)

    # Voice (optional; WS handled wherever you implement it)
    if _bool_env("FEATURE_AUDIO", True):
        _register("routes.voice", None)

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------
    @app.errorhandler(Exception)
    def _unhandled_error(err: Exception):
        # Pass through HTTP exceptions (e.g., 404, 405) so they are not mis-logged as 500
        if isinstance(err, HTTPException):
            return err
        try:
            app.logger.error("Unhandled server error", exc_info=err)
        finally:
            path = request.path or ""
            if path.startswith("/api/"):
                return jsonify(ok=False, error="server_error"), 500
            return "Internal Server Error", 500

    return app


# Expose 'app' for 'gunicorn app:app'
app = create_app()
