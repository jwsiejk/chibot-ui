# app/__init__.py
from flask import Flask, render_template
from .api_v1 import create_v1_blueprint
from .middleware.csrf import csrf_before_request
from .ws.chat_ws import register_ws_route

def create_app():
    # Keep your existing folders; these are correct if templates/ and static/ sit next to app/
    app = Flask(__name__, static_folder='../static', template_folder='../templates')
    app.config['JSON_SORT_KEYS'] = False

    # TODO: switch to env in prod (e.g., from app.config.Settings)
    app.secret_key = "test-secret"

    # v1-only API
    app.register_blueprint(create_v1_blueprint(), url_prefix="/api/v1")

    # CSRF
    app.before_request(csrf_before_request)

    # Optional helper: returns 426 for HTTP hits to the WS path
    register_ws_route(app)

    # --- New: UI routes so / and /admin load pages instead of 404/500 ---
    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/admin")
    def admin_ui():
        return render_template("admin.html")

    return app
