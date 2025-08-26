from __future__ import annotations

def create_app():
    import os, importlib, logging
    from flask import Flask, jsonify, render_template, request, session
    from utils.call_log import call_log

    app = Flask(__name__, template_folder="../templates", static_folder="../static", static_url_path="/static")
    app.secret_key = os.getenv("SECRET_KEY") or os.getenv("FLASK_SECRET") or "dev-secret-change-me"
    app.logger.setLevel(logging.INFO)

    def _register(mod_path: str, attr: str, url_prefix: str | None = None):
        try:
            mod = importlib.import_module(mod_path)
            bp = getattr(mod, attr)
            app.register_blueprint(bp, url_prefix=url_prefix)
            app.logger.info("Registered %s at %s", mod_path, url_prefix or "/")
            return True
        except Exception as e:
            app.logger.warning("Blueprint '%s' not registered: %s", mod_path, e)
            return False

    # Blueprints (chat & voice expect these prefixes)
    _register("routes.voice", "voice_bp", "/api/voice")
    _register("routes.chat", "chat_bp", "/api/chat")
    _register("routes.greet", "bp")              # greet has url_prefix="/api" internally
    _register("routes.admin", "admin_bp", "/admin")

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/health")
    def api_health():
        def any_env(*names): return any(os.getenv(n, "").strip() for n in names)
        return jsonify({
            "openai_configured": bool(os.getenv("OPENAI_API_KEY", "").strip()),
            "eleven_configured": any_env("ELEVENLABS_API_KEY", "ELEVEN_API_KEY", "XI_API_KEY"),
            "database_configured": bool(os.getenv("DATABASE_URL","").strip()),
            "is_admin": True if not os.getenv("ADMIN_EMAILS") else (session.get("email") or "").lower() in [e.strip().lower() for e in os.getenv("ADMIN_EMAILS","").split(",") if e.strip()],
        })

    # JSON aliases kept for backward-compatibility with earlier UI code
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

    return app
