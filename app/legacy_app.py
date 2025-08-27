from __future__ import annotations

import os
import logging
from flask import Flask, jsonify, render_template, request, session
from werkzeug.exceptions import HTTPException

# Optional services
import memory
from utils.call_log import call_log


def _bool_env(*names: str) -> bool:
    for n in names:
        v = os.getenv(n, "").strip()
        if v:
            return True
    return False


def create_app():
    app = Flask(
        __name__,
        template_folder="../templates",
        static_folder="../static",
        static_url_path="/static",
    )
    app.secret_key = os.getenv("SECRET_KEY") or os.getenv("FLASK_SECRET") or "dev-secret-change-me"
    app.logger.setLevel(logging.INFO)
    app.json.sort_keys = False

    # --- DB bootstrap (no-op if DATABASE_URL not set) ---
    try:
        memory.init_db()
    except Exception as e:
        app.logger.warning("DB init skipped: %s", e)

    # --- Helper: safe dynamic blueprint registration with duplicate guard ---
    def _register(mod_path: str, attr: str, url_prefix: str | None = None, name: str | None = None):
        """
        Import a blueprint by module + attribute and register it only if a blueprint
        with the same (intended) name is not already registered.
        """
        try:
            mod = __import__(mod_path, fromlist=[attr])
            bp = getattr(mod, attr)
            bp_name = name or getattr(bp, "name", attr)
            if bp_name in app.blueprints:
                app.logger.info(
                    "Skipping blueprint %s.%s: name '%s' already registered",
                    mod_path, attr, bp_name
                )
                return
            if name:
                app.register_blueprint(bp, url_prefix=url_prefix, name=name)
            else:
                app.register_blueprint(bp, url_prefix=url_prefix)
            app.logger.info("Registered blueprint %s as %s", mod_path, url_prefix or "(inline)")
        except Exception as e:
            app.logger.warning("Skipping blueprint %s.%s: %s", mod_path, attr, e)

    # --- Core blueprints (guarded to avoid duplicate registration warnings) ---

    # Voice API: register once at /api/voice
    try:
        from routes.voice import voice_bp as _voice_bp
        if "voice_bp" not in app.blueprints:
            app.register_blueprint(_voice_bp, url_prefix="/api/voice")
        else:
            app.logger.info("voice_bp already registered; skipping duplicate")
    except Exception as e:
        app.logger.warning("Skipping voice blueprint: %s", e)

    # Admin UI (factory) — mounted at both /admin and /api/admin with unique names
    try:
        from routes.admin import create_admin_blueprint as _cab
        if "admin_ui" not in app.blueprints:
            app.register_blueprint(_cab("admin_ui"), url_prefix="/admin")
        else:
            app.logger.info("admin_ui already registered; skipping duplicate")
        if "admin_api" not in app.blueprints:
            app.register_blueprint(_cab("admin_api"), url_prefix="/api/admin")
        else:
            app.logger.info("admin_api already registered; skipping duplicate")
    except Exception as e:
        app.logger.warning("Skipping admin blueprints: %s", e)

    # Tools (diagnostics + static admin viewer)
    try:
        from routes.tools import tools_bp as _tools_bp
        if "tools_bp" not in app.blueprints:
            app.register_blueprint(_tools_bp)  # /askchip-diagnostics.html, /admin-log.html
        else:
            app.logger.info("tools_bp already registered; skipping duplicate")
    except Exception as e:
        app.logger.warning("Skipping tools blueprint: %s", e)

    # Profile API (safe session-first GET/POST /api/profile)
    try:
        from routes.profile import profile_bp as _profile_bp
        if "profile_bp" not in app.blueprints:
            app.register_blueprint(_profile_bp, url_prefix="/api")
        else:
            app.logger.info("profile_bp already registered; skipping duplicate")
    except Exception as e:
        app.logger.warning("Skipping profile blueprint: %s", e)

    # --- Additional feature blueprints via dynamic loader (kept from your file) ---

    # Chat (REST) — some repos mount this at /api/chat/<subroutes>; keep it, but we also add a root fallback below.
    _register("routes.chat", "chat_bp", url_prefix="/api/chat")

    # Conversation (SSE stream) — provides /api/conversation
    _register("routes.conversation", "conversation_bp", url_prefix=None)

    # Greet — already scoped to /api in that module
    _register("routes.greet", "bp", url_prefix=None)

    # ---------- Error handling (force JSON so the UI never sees non_json_response) ----------
    @app.errorhandler(HTTPException)
    def _http_error(e: HTTPException):
        payload = {
            "ok": False,
            "status": e.code,
            "error": e.name,
            "detail": (e.description or "").strip(),
            "path": request.path,
        }
        return jsonify(payload), e.code

    @app.errorhandler(Exception)
    def _uncaught(e: Exception):
        app.logger.exception("Unhandled server error")
        return jsonify({
            "ok": False,
            "status": 500,
            "error": "Internal Server Error",
            "detail": str(e),
            "path": request.path
        }), 500

    # ---------- Helpers ----------
    def _current_user_email() -> str | None:
        return (session.get("user", {}) or {}).get("email") or session.get("email")

    def _is_admin_flag() -> bool:
        email = _current_user_email()
        admin_env = (os.getenv("ASKCHIP_ADMIN_UI", "") or "").strip().lower()
        return bool(email) and admin_env not in ("off", "false", "0")

    # ---------- Health ----------
    @app.get("/api/health")
    def api_health():
        return jsonify({
            "ok": True,
            "openai_configured": bool(os.getenv("OPENAI_API_KEY", "").strip()),
            "eleven_configured": _bool_env("ELEVENLABS_API_KEY", "ELEVEN_API_KEY", "XI_API_KEY")
                                 and _bool_env("ELEVENLABS_VOICE_ID", "ELEVEN_VOICE_ID", "CHIP_VOICE_ID"),
            "db": bool(os.getenv("DATABASE_URL", "").strip()),
            "is_admin": _is_admin_flag(),
        })

    # Some clients call /health — return the same payload so the Admin overlay can key off it
    @app.get("/health")
    def health():
        return jsonify({
            "ok": True,
            "openai_configured": bool(os.getenv("OPENAI_API_KEY", "").strip()),
            "eleven_configured": _bool_env("ELEVENLABS_API_KEY", "ELEVEN_API_KEY", "XI_API_KEY")
                                 and _bool_env("ELEVENLABS_VOICE_ID", "ELEVEN_VOICE_ID", "CHIP_VOICE_ID"),
            "db": bool(os.getenv("DATABASE_URL", "").strip()),
            "is_admin": _is_admin_flag(),
        })

    # ---------- Auth + Profile (login/me/logout) ----------
    @app.post("/api/login")
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

    @app.post("/api/logout")
    def api_logout():
        session.pop("email", None)
        return jsonify({"ok": True})

    @app.get("/api/me")
    def api_me():
        email = _current_user_email()
        if not email:
            return jsonify({"ok": True, "logged_in": False})
        user = memory.get_user(email) or {"email": email}
        profile_complete = bool((user or {}).get("name"))
        return jsonify({"ok": True, "logged_in": True, "profile_complete": profile_complete, "user": user})

    # NOTE: Inline /api/profile endpoints were removed to avoid colliding with the blueprint.
    # The profile routes now live under routes.profile (registered above).

    # ---------- /api/chat (JSON fallback) ----------
    # Some front-ends POST to /api/chat expecting JSON { ok, reply|text, ... }.
    # If no existing rule handles that exact route+method, register a safe fallback.
    def _route_exists(rule: str, method: str) -> bool:
        method = method.upper()
        for r in app.url_map.iter_rules():
            if r.rule == rule and method in (r.methods or set()):
                return True
        return False

    def _chat_fallback():
        data = request.get_json(silent=True) or {}
        user_text = (data.get("text") or data.get("message") or data.get("prompt") or "").strip()
        if not user_text:
            return jsonify({"ok": False, "error": "Missing 'text' in request body"}), 400

        # Try OpenAI; if not configured/available, return a concise echo answer.
        reply = None
        try:
            from openai import OpenAI  # type: ignore
            client = OpenAI()
            model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
            sys = "You are Chip, a concise, helpful Pure Storage systems engineer. Keep answers actionable."
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": sys}, {"role": "user", "content": user_text}],
                temperature=float(os.getenv("OPENAI_T", "0.6")),
                max_tokens=512,
            )
            reply = (resp.choices[0].message.content or "").strip()
        except Exception:
            reply = f"Here's a concise response to get you moving:\n\n- {user_text}"

        return jsonify({"ok": True, "reply": reply, "text": reply})

    if not _route_exists("/api/chat", "POST"):
        app.add_url_rule("/api/chat", endpoint="api_chat_fallback", view_func=_chat_fallback, methods=["POST"])
        app.logger.info("Registered /api/chat POST fallback (JSON)")

    # ---------- Phrase / Follow-up / Nudge (safe fallbacks) ----------
    @app.post("/api/phrase")
    def api_phrase():
        return jsonify({"ok": True, "text": ""})

    @app.post("/api/followup")
    def api_followup():
        return jsonify({
            "ok": True,
            "suggestions": [
                "Give me a concise summary.",
                "List the next 3 steps.",
                "Any risks or prerequisites?"
            ]
        })

    @app.post("/api/nudge")
    def api_nudge():
        return jsonify({"ok": True, "text": "I can summarize or dive deeper—what would help most?"})

    # ---------- Admin: call log JSON (UI & stream live under /admin/*) ----------
    @app.get("/api/admin/calls/recent")
    def api_admin_calls_recent():
        try:
            limit = int(request.args.get("limit") or 200)
        except Exception:
            limit = 200
        return jsonify(call_log.recent(limit))

    @app.post("/api/admin/calls/clear")
    def api_admin_calls_clear():
        call_log.clear()
        return jsonify({"ok": True})

    # ---------- Root ----------
    @app.get("/")
    def index():
        return render_template("index.html")

    return app
