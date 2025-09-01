from __future__ import annotations

import os
import logging
import importlib
import secrets
from datetime import datetime as _dt, timedelta
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

    # ------------------------------------------------------------------
    # Base config
    # ------------------------------------------------------------------
    app.config["JSON_SORT_KEYS"] = False
    app.config["JSON_AS_ASCII"] = False
    app.config["TEMPLATES_AUTO_RELOAD"] = True

    # ------------------------------------------------------------------
    # SECRET_KEY (must be set BEFORE any session use)
    # Supports your legacy env name plus the conventional one.
    # ------------------------------------------------------------------
    secret = (
        os.environ.get("SECRET_KEY")
        or os.environ.get("FLASK_SECRET")
        or os.environ.get("Flask_Secret")  # legacy casing you mentioned
    )

    if not secret:
        # Dev-only fallback; DO NOT rely on this in production.
        if app.debug:
            secret = secrets.token_hex(32)
            app.logger.warning(
                "Generated dev SECRET_KEY; set SECRET_KEY (or FLASK_SECRET/Flask_Secret) in env for prod."
            )
        else:
            raise RuntimeError(
                "SECRET_KEY is required (checked env: SECRET_KEY, FLASK_SECRET, Flask_Secret)."
            )

    # Direct assignment (not setdefault) — Flask predefines SECRET_KEY=None.
    app.config["SECRET_KEY"] = secret

    # Sensible session cookie defaults
    app.config.setdefault("SESSION_COOKIE_NAME", "askchip_session")
    app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")
    app.config.setdefault("SESSION_COOKIE_SECURE", not app.debug)  # True on HTTPS (Render)
    app.config.setdefault("SESSION_PERMANENT", True)
    app.config.setdefault("PERMANENT_SESSION_LIFETIME", timedelta(hours=12))

    if not app.debug and not app.testing:
        logging.basicConfig(level=logging.INFO)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _register(
        module_path: str,
        attr_name: Optional[str] = None,
        url_prefix: Optional[str] = None,
    ) -> None:
        """Import module and register its Blueprint attribute safely."""
        try:
            mod = importlib.import_module(module_path)
        except Exception as e:  # pragma: no cover
            app.logger.info("Skipping %s: import failed: %s", module_path, e)
            return

        names = (
            [attr_name]
            if attr_name
            else [
                "bp",
                "blueprint",
                "api_bp",
                "chat_bp",
                "profile_bp",
                "tools_bp",
                "admin_bp",
                "email_bp",
                "voice_bp",
                "auth_bp",
            ]
        )
        bp = None
        for nm in names:
            if nm and hasattr(mod, nm):
                bp = getattr(mod, nm)
                break
        if bp is None:
            # Best-effort scan (works for objects that "look like" Blueprints)
            for nm in dir(mod):
                obj = getattr(mod, nm)
                if (
                    getattr(obj, "register", None)
                    and getattr(obj, "name", None)
                    and getattr(obj, "url_prefix", None) is not None
                ):
                    bp = obj
                    break
        if bp is None:
            app.logger.info("Skipping %s: no blueprint found", module_path)
            return
        try:
            if getattr(bp, "name", None) in app.blueprints:
                app.logger.info("Blueprint %s already registered; skipping", bp.name)
            else:
                app.register_blueprint(bp, url_prefix=url_prefix)
                app.logger.info(
                    "Registered blueprint from %s as %s (url_prefix=%r)",
                    module_path,
                    getattr(bp, "name", "?"),
                    url_prefix,
                )
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
        return jsonify(
            {
                "ok": True,
                "authenticated": bool(email),
                "profileComplete": profile_complete,
                "first_time": not profile_complete,
                "email": email,
                "profile": profile,
                # legacy keys for older UIs
                "logged_in": bool(email),
                "profile_complete": profile_complete,
            }
        )

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
    # CORS for voice endpoints (to support separate UI origin)
    # ------------------------------------------------------------------
    @app.after_request
    def _voice_cors(resp):
        try:
            p = request.path or ""
            if p.startswith(("/api/voice", "/voice", "/eleven")):
                resp.headers.setdefault("Access-Control-Allow-Origin", "*")
                resp.headers.setdefault("Access-Control-Allow-Headers", "Content-Type, Authorization")
                resp.headers.setdefault("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        except Exception:
            pass
        return resp

    # ------------------------------------------------------------------
    # Voice fallback routes (only if blueprint didn't register expected paths)
    # ------------------------------------------------------------------
    def _install_voice_fallbacks() -> None:
        try:
            existing_paths = {rule.rule for rule in app.url_map.iter_rules()}
        except Exception:
            existing_paths = set()

        def _path_missing(p: str) -> bool:
            return p not in existing_paths

        def _extract_text(payload: dict) -> str:
            try:
                return (
                    payload.get("text")
                    or payload.get("input")
                    or payload.get("message")
                    or payload.get("utterance")
                    or ""
                ).strip()
            except Exception:
                return ""

        def _synthesize_local(text: str):
            # Try project bridge first; fall back to urllib if unavailable.
            try:
                from services.tts_bridge import synthesize_with_visemes  # type: ignore
                return synthesize_with_visemes(text)
            except Exception:
                pass

            # Minimal urllib fallback (no visemes)
            import base64, json as _json, urllib.request, urllib.error
            api_key = (
                os.getenv("ELEVENLABS_API_KEY")
                or os.getenv("ELEVEN_API_KEY")
            )
            voice_id = (
                os.getenv("ELEVENLABS_VOICE_ID")
                or os.getenv("ELEVEN_VOICE_ID")
                or os.getenv("CHIP_VOICE_ID")
                or ""
            ).strip()
            model_id = os.getenv("ELEVENLABS_MODEL_ID", "eleven_turbo_v2")

            if not api_key or not voice_id:
                return None, None, "not_configured"

            url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
            headers = {
                "accept": "audio/mpeg",
                "content-type": "application/json",
                "xi-api-key": api_key,
            }
            payload = {
                "text": text,
                "model_id": model_id,
                # Let server defaults handle voice settings if not provided
            }
            try:
                req = urllib.request.Request(url, data=_json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=60) as resp:
                    content = resp.read()
                audio_b64 = base64.b64encode(content).decode("utf-8")
                return audio_b64, None, None
            except urllib.error.HTTPError as e:
                try:
                    err_body = e.read().decode("utf-8")
                except Exception:
                    err_body = str(e)
                return None, None, f"HTTP {e.code}: {err_body[:200]}"
            except Exception as e:
                return None, None, f"tts_exception: {e!r}"

        def _tts_handler():
            try:
                payload = request.get_json(silent=True) or {}
            except Exception:
                payload = {}
            text = _extract_text(payload) or (request.args.get("text") or "").strip()

            if not text:
                return jsonify({"ok": False, "error": "no_text", "status": 400, "detail": "Provide text/input/message/utterance."}), 400

            audio_b64, visemes, err = _synthesize_local(text)
            if err:
                status = 200 if err == "not_configured" else 502
                return jsonify({"ok": False, "error": "tts_failed", "detail": err, "status": status}), status

            resp = {"ok": True, "audio": audio_b64}
            if visemes:
                resp["visemes"] = visemes
            return jsonify(resp), 200

        # Paths various UIs might probe
        fallback_paths = [
            "/api/voice/tts_with_visemes",
            "/api/voice/speak",
            "/api/voice/tts",
            "/voice/speak",
            "/voice/tts",
            "/eleven/tts",
            "/eleven/speak",
        ]
        added = 0
        for ix, path in enumerate(fallback_paths):
            if _path_missing(path):
                try:
                    app.add_url_rule(path, endpoint=f"voice_fallback_{ix}", view_func=_tts_handler, methods=["POST"])
                    added += 1
                except Exception:
                    pass

        # health endpoints (optional)
        def _health():
            configured = bool(
                (os.getenv("ELEVENLABS_API_KEY") or os.getenv("ELEVEN_API_KEY"))
                and (os.getenv("ELEVENLABS_VOICE_ID") or os.getenv("ELEVEN_VOICE_ID") or os.getenv("CHIP_VOICE_ID"))
            )
            return jsonify({"ok": True, "configured": configured})
        for p in ("/voice/health", "/api/voice/health"):
            if _path_missing(p):
                try:
                    app.add_url_rule(p, endpoint=f"voice_fallback_health_{p}", view_func=_health, methods=["GET"])
                except Exception:
                    pass

        if added:
            app.logger.info("Installed %d voice fallback route(s).", added)

    # Install fallbacks after any blueprint registration
    try:
        _install_voice_fallbacks()
    except Exception as e:  # pragma: no cover
        app.logger.warning("Voice fallbacks failed: %s", e)

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------
    @app.errorhandler(Exception)
    def _unhandled_error(err: Exception):
        path = (request.path or "")
        wants_json = path.startswith(("/api/", "/voice", "/api/voice", "/speak"))

        # If it's a Flask HTTPException (404, 405, etc.)
        if isinstance(err, HTTPException):
            if wants_json:
                payload = {
                    "ok": False,
                    "error": err.name,
                    "status": err.code,
                }
                if getattr(err, "description", None) and err.description != err.name:
                    payload["detail"] = err.description
                return jsonify(payload), err.code
            return err  # default HTML for non-API paths like normal pages

        # Non-HTTP exceptions (tracebacks)
        app.logger.error("Unhandled server error", exc_info=err)
        if wants_json:
            return jsonify(ok=False, error="server_error"), 500
        return "Internal Server Error", 500

    return app


# Expose 'app' for 'gunicorn app:app'
app = create_app()
