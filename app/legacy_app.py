from __future__ import annotations

import os
import json
import time
import datetime as _dt
import logging
from flask import Flask, jsonify, render_template, request, session, Response, stream_with_context, g
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

    # ---------- Server-side enforcement: require complete profile for protected endpoints ----------
    @app.before_request
    def _require_profile_for_protected():
        # Allow CORS preflight and non-protected routes
        if request.method == "OPTIONS":
            return None

        p = (request.path or "").rstrip("/")
        # Explicitly protect LLM-related endpoints only
        protected_starts = ("/api/chat", "/api/conversation", "/api/greet", "/api/voice")
        if not any(p.startswith(s) for s in protected_starts):
            return None

        email = _current_user_email()
        if not email:
            # Let existing auth logic handle missing auth (401/redirect) elsewhere
            return None

        try:
            user = memory.get_user(email) or {}
        except Exception:
            user = {}

        required = ("email", "name", "title", "region")
        if not all(user.get(k) for k in required):
            return jsonify({"ok": False, "status": 428, "error": "PROFILE_INCOMPLETE"}), 428

        return None

    # ---------- After-request fixup: ensure /api/profile GET always includes session email ----------
    @app.after_request
    def _ensure_profile_email(resp):
        try:
            if request.method == "GET" and (request.path or "").rstrip("/") == "/api/profile":
                # Only touch JSON responses
                ctype = (resp.content_type or "")
                if "application/json" in ctype:
                    body = resp.get_data(as_text=True) or ""
                    data = json.loads(body) if body else {}
                    email = _current_user_email()
                    if email:
                        if isinstance(data, dict):
                            # Common shapes: { ... email? ... }, or { user: {...} }
                            if "user" in data and isinstance(data["user"], dict):
                                data["user"].setdefault("email", email)
                            else:
                                data.setdefault("email", email)
                            # Re-encode JSON
                            resp.set_data(json.dumps(data))
                            # Flask will keep the status/code/headers; ensure content-type is still json
                            resp.headers["Content-Type"] = "application/json; charset=utf-8"
            return resp
        except Exception:
            # Never break the response if anything goes wrong here
            return resp

    # ---------- Auth + Profile (login/me/logout) ----------
    @app.post("/api/login")
    def api_login():
        data = request.get_json(silent=True) or {}
        email = (data.get("email") or "").strip().lower()
        if not email or "@" not in email:
            return jsonify({"ok": False, "error": "Valid email required"}), 400
        session["email"] = email
        # IMPORTANT: Do not create/update the user here; this can clobber an existing profile with NULLs.
        # New users will be created when they POST /api/profile with real data.
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
        required = ("email", "name", "title", "region")
        profile_complete = all((user or {}).get(k) for k in required)
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


# ---------- Admin allow-list helper ----------
def _is_admin_email(email: str | None) -> bool:
    allowed = [e.strip().lower() for e in (os.getenv("ADMIN_EMAILS","")).split(",") if e.strip()]
    return bool(email and email.lower() in allowed)

# ---------- Admin Call Log page (uses templates/admin_call_log.html) ----------
@app.get("/admin/call-log")
def admin_call_log_page():
    email = (session.get("user", {}) or {}).get("email") or session.get("email")
    if not _is_admin_email(email):
        return render_template("admin_call_log.html", items=[]), 403
    try:
        items = call_log.recent(int(request.args.get("limit") or 200))
    except Exception:
        items = []
    return render_template("admin_call_log.html", items=items)

# ---------- Admin Call Log SSE stream ----------
@app.get("/admin/stream")
def admin_log_stream():
    email = (session.get("user", {}) or {}).get("email") or session.get("email")
    if not _is_admin_email(email):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    def gen():
        yield ":ok\n\n"  # keep-alive for proxies
        last_ts = ""
        while True:
            try:
                items = call_log.recent(500)
                for e in items:
                    ts = (e.get("ts") or "")
                    if ts <= last_ts:
                        continue
                    kind = e.get("kind") or (f'{e.get("method","")} {e.get("path","")}'.strip())
                    msg  = e.get("msg") or f'{e.get("method","")} {e.get("path","")} [{e.get("status","")}] {e.get("ms","")}ms'
                    payload = {"ts": ts or _dt.datetime.utcnow().isoformat()+"Z",
                               "kind": kind, "msg": msg, "text": e.get("text"), "error": e.get("error")}
                    yield "data: " + json.dumps(payload) + "\n\n"
                    last_ts = ts
            except Exception:
                yield ":heartbeat\n\n"
            time.sleep(2)

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return Response(stream_with_context(gen()), mimetype="text/event-stream", headers=headers)

# ---------- Generic request capture -> call_log (so the views have data) ----------
def _calllog_add(entry: dict):
    for fn in ("add", "append", "push", "log", "write", "put"):
        m = getattr(call_log, fn, None)
        if callable(m):
            try:
                m(entry)
                return
            except Exception:
                pass

@app.before_request
def _start_timer_for_log():
    try:
        g._t0 = time.time()
    except Exception:
        pass

@app.after_request
def _capture_call(resp):
    try:
        p = (request.path or "")
        if not p.startswith("/api"):
            return resp
        if p.startswith("/api/admin/calls"):
            return resp
        entry = {
            "ts": _dt.datetime.utcnow().isoformat() + "Z",
            "method": request.method,
            "path": p,
            "status": resp.status_code,
            "ms": int(max(0, (time.time() - getattr(g, "_t0", time.time())) * 1000)),
            "email": (session.get("user", {}) or {}).get("email") or session.get("email"),
            "ip": request.headers.get("X-Forwarded-For", request.remote_addr),
            "qs": request.query_string.decode() if request.query_string else "",
        }
        _calllog_add(entry)
    finally:
        return resp

    return app
