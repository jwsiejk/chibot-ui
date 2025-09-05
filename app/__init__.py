# app/__init__.py
from flask import Flask, render_template, request
from .api_v1 import create_v1_blueprint
from .middleware.csrf import csrf_before_request
from .middleware.rate_limit import register_before_request as rate_limit_register
from .ws.chat_ws import register_ws_route

def create_app():
    # Keep your existing folders; these are correct if templates/ and static/ sit next to app/
    app = Flask(__name__, static_folder='../static', template_folder='../templates')
    app.config['JSON_SORT_KEYS'] = False

    # TODO: switch to env in prod (e.g., from app.config.Settings)
    app.secret_key = "test-secret"

    # v1-only API
    try:
        import os
        if os.environ.get('DATABASE_URL'):
            from .dal import neon_pg
            neon_pg.ensure_schema()
    except Exception:
        pass
    rate_limit_register(app)
    app.register_blueprint(create_v1_blueprint(), url_prefix="/api/v1")

    # CSRF
    app.before_request(csrf_before_request)

    # Optional helper: returns 426 for HTTP hits to the WS path
    register_ws_route(app)

    # --- New: UI routes so / and /admin load pages instead of 404/500 ---
    @app.get("/")
    def index():
        # Quick probe: ?raw=1 shows plain text so we can isolate template issues
        if request.args.get("raw") == "1":
            return "index-ok", 200, {"Content-Type": "text/plain; charset=utf-8"}
        return render_template("index.html")

    @app.get("/admin")
    def admin_ui():
        return render_template("admin.html")

    @app.get("/diagnostics")
    def diagnostics():
        return render_template("diagnostics.html")

    return app
