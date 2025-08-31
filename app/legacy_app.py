from __future__ import annotations

import os
import json
import logging
import importlib
from datetime import datetime as _dt
from pathlib import Path
from typing import Any, Optional, Dict

from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    session,
)

# --- Optional internal deps (fail-safe stubs if missing) ---------------------

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


# --- Env helpers -------------------------------------------------------------

def _bool_env(*names: str, default: bool = False) -> bool:
    """
    Return True if any of the given env vars are set to a truthy value.
    """
    truthy = {"1", "true", "yes", "on"}
    for n in names:
        v = os.getenv(n)
        if v is not None and v.strip().lower() in truthy:
            return True
    return default


def _str_env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return default if v is None else v


# --- App factory -------------------------------------------------------------

def create_app() -> Flask:
    """
    Application factory. Points Flask at repo-root templates/static, registers
    only the canonical blueprints, and exposes a small /api surface.
    """
    # Project root is two levels up: /.../src/app/legacy_app.py -> /.../src
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

    # Logging baseline
    if not app.debug and not app.testing:
        logging.basicConfig(level=logging.INFO)

    # -------------------------------------------------------------------------
    # Small helper to register optional blueprints safely (no legacy convo)
    # -------------------------------------------------------------------------
    def _register(module_path: str, attr_name: Optional[str] = None, url_prefix: Optional[str] = None) -> None:
        """
        Import module and register a Flask Blueprint attribute on it. If attr_name
        is None, try common attribute names.
        """
        try:
            mod = importlib.import_module(module_path)
        except Exception as e:  # pragma: no cover
            app.logger.info("Skipping %s: import failed: %s", module_path, e)
            return

        candidate_names = [attr_name] if attr_name else ["bp", "blueprint", "api_bp", "chat_bp", "profile_bp", "tools_bp", "admin_bp", "email_bp", "voice_bp"]
        bp_obj = None
        for nm in candidate_names:
            if not nm:
                continue
            if hasattr(mod, nm):
                bp_obj = getattr(mod, nm)
                break
        if bp_obj is None:
            # try: first Blueprint-looking attribute
            for nm in dir(mod):
                obj = getattr(mod, nm)
                if getattr(obj, "register", None) and getattr(obj, "name", None) and getattr(obj, "url_prefix", None) is not None:
                    bp_obj = obj
                    break

        if bp_obj is None:
            app.logger.info("Skipping %s: no blueprint attribute found", module_path)
            return

        try:
            if bp_obj.name in app.blueprints:
                app.logger.info("Blueprint %s already registered; skipping", bp_obj.name)
            else:
                app.register_blueprint(bp_obj, url_prefix=url_prefix)
                app.logger.info("Registered blueprint from %s as %s (url_prefix=%r)", module_path, getattr(bp_obj, "name", "?"), url_prefix)
        except Exception as e:  # pragma: no cover
            app.logger.warning("Failed registering blueprint %s: %s", module_path, e)

    # -------------------------------------------------------------------------
    # Core routes & pages
    # -------------------------------------------------------------------------

    @app.get("/")
    def index():
        # Render repo-root templates/index.html
        return render_template("index.html")

    @app.get("/healthz")
    def healthz():
        return jsonify(ok=True, time=_dt.utcnow().isoformat() + "Z")

    # Features used by the UI to decide what to show. Keep it simple & explicit.
    @app.get("/api/features")
    def api_features():
        data = {
            "AUDIO": _bool_env("FEATURE_AUDIO", default=True),       # keep your WS/VAD visible unless you turn it off
            "HISTORY": _bool_env("FEATURE_HISTORY", default=False),
            "ADMIN_UI": _bool_env("FEATURE_ADMIN_UI", default=False),
            "TOOLS": _bool_env("FEATURE_TOOLS", default=False),
            "EMAIL": _bool_env("FEATURE_EMAIL", default=True),
            "ACCOUNTS": _bool_env("FEATURE_ACCOUNTS", default=False),
        }
        return jsonify(ok=True, features=data)

    # Session/profile surface for gating
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
        # Aliases to keep both old and new frontends happy
        payload = {
            "ok": True,
            "authenticated": bool(email),
            "profileComplete": profile_complete,
            "first_time": not profile_complete,
            "email": email,
            "profile": profile,
            # legacy keys
            "logged_in": bool(email),
            "profile_complete": profile_complete,
        }
        return jsonify(payload)

    # -------------------------------------------------------------------------
    # Blueprints (NO conversation/orchestrator registration)
    # -------------------------------------------------------------------------

    # Diagnostics / tools (optional)
    if _bool_env("FEATURE_TOOLS"):
        _register("routes.tools", "tools_bp")  # /askchip-diagnostics.html, /admin-log.html

    # Admin UI & SSE log stream (optional)
    if _bool_env("FEATURE_ADMIN_UI"):
        _register("routes.admin", "admin_bp")

    # Profile API: the module defines /profile; we mount at /api via url_prefix.
    try:
        from routes.profile import profile_bp as _profile_bp  # type: ignore
        if "profile_bp" not in app.blueprints:
            app.register_blueprint(_profile_bp, url_prefix="/api")
        else:
            app.logger.info("profile_bp already registered; skipping duplicate")
    except Exception as e:  # pragma: no cover
        app.logger.info("Profile blueprint not available: %s", e)

    # Greet (module already has url_prefix='/api')
    _register("routes.greet", "bp", url_prefix=None)

    # Chat (canonical /api/chat). If the module sets its own url_prefix, we don't add another.
    _register("routes.chat", "chat_bp", url_prefix=None)

    # Email API (optional; module may already bind under /api)
    if _bool_env("FEATURE_EMAIL", default=True):
        _register("routes.email_api", None, url_prefix=None)

    # Accounts search (optional)
    if _bool_env("FEATURE_ACCOUNTS"):
        _register("routes.accounts", None, url_prefix=None)

    # Voice routes (optional; keep your WS/VAD pipeline untouched)
    if _bool_env("FEATURE_AUDIO", default=True):
        _register("routes.voice", None, url_prefix=None)
        # If you expose a WS endpoint via a separate module, it remains as implemented.

    # -------------------------------------------------------------------------
    # Error handlers
    # -------------------------------------------------------------------------

    @app.errorhandler(Exception)
    def _unhandled_error(err: Exception):
        try:
            app.logger.error("Unhandled server error", exc_info=err)
        finally:
            # Avoid leaking internals; keep response simple JSON for API routes
            path = request.path or ""
            if path.startswith("/api/"):
                return jsonify(ok=False, error="server_error"), 500
            # For page routes, a minimal message; index can surface its own UI
            return "Internal Server Error", 500

    return app


# Expose module-level 'app' for 'gunicorn app:app'
app = create_app()
