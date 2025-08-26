
from __future__ import annotations

import os, logging
from flask import Flask, jsonify, render_template, request, session

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
    app = Flask(__name__, template_folder="../templates", static_folder="../static", static_url_path="/static")
    app.secret_key = os.getenv("SECRET_KEY") or os.getenv("FLASK_SECRET") or "dev-secret-change-me"
    app.logger.setLevel(logging.INFO)
    app.json.sort_keys = False

    # --- DB bootstrap (no-op if DATABASE_URL not set) ---
    try:
        memory.init_db()
    except Exception as e:
        app.logger.warning("DB init skipped: %s", e)

    # --- Blueprint registry helpers ---
    def _register(mod_path: str, attr: str, url_prefix: str | None = None):
        try:
            mod = __import__(mod_path, fromlist=[attr])
            bp = getattr(mod, attr)
            if url_prefix:
                app.register_blueprint(bp, url_prefix=url_prefix)
            else:
                app.register_blueprint(bp)
            app.logger.info("Registered blueprint %s as %s", mod_path, url_prefix or "(inline)")
        except Exception as e:
            app.logger.warning("Skipping blueprint %s.%s: %s", mod_path, attr, e)

    # Chat (REST) — final route: /api/chat
    _register("routes.chat", "chat_bp", url_prefix="/api/chat")

    # Conversation (SSE stream) — provides /api/conversation
    _register("routes.conversation", "conversation_bp", url_prefix=None)

    # Greet — already scoped to /api in the file
    _register("routes.greet", "bp", url_prefix=None)

    # Voice (ElevenLabs bridge) — final routes under /api/voice/*
    _register("routes.voice", "voice_bp", url_prefix="/api/voice")

    # Admin page & SSE stream under /admin; JSON helpers are provided below
    _register("routes.admin", "admin_bp", url_prefix="/admin")

    # ---------- Health ----------
    @app.get("/api/health")
    def api_health():
        return jsonify({
            "ok": True,
            "openai_configured": bool(os.getenv("OPENAI_API_KEY", "").strip()),
            "eleven_configured": _bool_env("ELEVENLABS_API_KEY", "ELEVEN_API_KEY", "XI_API_KEY")
                                 and _bool_env("ELEVENLABS_VOICE_ID", "ELEVEN_VOICE_ID", "CHIP_VOICE_ID"),
            "db": bool(os.getenv("DATABASE_URL", "").strip()),
        })

    @app.get("/health")
    def health():
        return jsonify({"ok": True})

    # ---------- Auth + Profile ----------
    def current_user_email() -> str | None:
        return (session.get("user", {}) or {}).get("email") or session.get("email")

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
        email = current_user_email()
        if not email:
            return jsonify({"ok": True, "logged_in": False})
        user = memory.get_user(email) or {"email": email}
        profile_complete = bool((user or {}).get("name"))
        return jsonify({"ok": True, "logged_in": True, "profile_complete": profile_complete, "user": user})

    @app.route("/api/profile", methods=["GET", "POST"])
    def api_profile():
        email = current_user_email()
        if not email:
            return jsonify({"ok": False, "error": "Not authenticated"}), 401
        if request.method == "GET":
            user = memory.get_user(email) or {"email": email}
            return jsonify({"ok": True, "user": user})
        data = request.get_json(silent=True) or {}
        memory.save_user(
            email=email,
            name=data.get("name"),
            title=data.get("title"),
            region=data.get("region"),
            profile=data.get("profile"),
        )
        user = memory.get_user(email) or {"email": email}
        return jsonify({"ok": True, "user": user})

    # ---------- Email + Accounts ----------
    @app.post("/api/email/send")
    def api_email_send():
        from services.email_service import send_email
        data = request.get_json(silent=True) or {}
        to = data.get("to") or []
        if isinstance(to, str):
            to = [to]
        subject = data.get("subject") or "(no subject)"
        html = data.get("html") or None
        text = data.get("text") or None
        reply_to = data.get("reply_to") or None
        ok = False
        try:
            ok = send_email(to=to, subject=subject, html=html, text=text, reply_to=reply_to)
        except Exception as e:
            app.logger.warning("email_send failed: %s", e)
        return jsonify({"ok": bool(ok)})

    @app.get("/api/accounts/search")
    def api_accounts_search():
        from services.accounts_service import search_accounts
        q = (request.args.get("q") or "").strip()
        try:
            items = search_accounts(q, limit=20)
        except Exception as e:
            app.logger.warning("accounts_search failed: %s", e)
            items = []
        return jsonify({"ok": True, "items": items})

    # ---------- Phrase / Follow-up / Nudge (safe fallbacks) ----------
    @app.post("/api/phrase")
    def api_phrase():
        # Server-side phrasing can be added later; provide a safe default.
        return jsonify({"ok": True, "text": ""})

    @app.post("/api/followup")
    def api_followup():
        # Provide three sensible defaults if the client asks.
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

    

    # ---------- Voice health (for diagnostics page) ----------
    @app.get("/api/voice/health")
    def api_voice_health():
        return jsonify({
            "ok": True,
            "configured": _bool_env("ELEVENLABS_API_KEY", "ELEVEN_API_KEY", "XI_API_KEY")
                          and _bool_env("ELEVENLABS_VOICE_ID", "ELEVEN_VOICE_ID", "CHIP_VOICE_ID"),
            "model": os.getenv("ELEVENLABS_MODEL_ID", "eleven_turbo_v2")
        })
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
