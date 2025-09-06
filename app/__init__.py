# app/__init__.py
import os
from flask import Flask, Blueprint, render_template, send_from_directory, request

from .api_v1 import create_v1_blueprint
from .middleware.csrf import csrf_before_request
from .middleware.rate_limit import register_before_request as rate_limit_register

# --- Core blueprint (docs/misc) ---
core_bp = Blueprint('core', __name__)

_MODULE_DIR = os.path.dirname(__file__)
_docs_dir = os.path.abspath(os.path.join(_MODULE_DIR, "..", "docs"))

@core_bp.get("/docs/<path:fname>")
def serve_docs(fname):
    return send_from_directory(_docs_dir, fname)

def create_app():
    # Serve /static (JS/CSS/img) and /templates
    app = Flask(
        __name__,
        static_folder='../static',
        static_url_path='/static',
        template_folder='../templates'
    )
    app.config['JSON_SORT_KEYS'] = False

    # Secret key (read env in production)
    app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

    # Register v1-only API
    api_bp = create_v1_blueprint()
    app.register_blueprint(api_bp, url_prefix="/api/v1")

    # Register core docs/static blueprint
    app.register_blueprint(core_bp)

    # Basic pages
    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/admin")
    def admin():
        return render_template("admin.html")

    @app.get("/diagnostics")
    def diagnostics():
        return render_template("diagnostics.html")

    # Favicon (browsers request /favicon.ico by default)
    @app.get("/favicon.ico")
    def favicon():
        return send_from_directory(app.static_folder, "favicon.ico", mimetype="image/x-icon")

    # Middleware
    app.before_request(csrf_before_request)
    rate_limit_register(app)

    # Simple CORS (optional, controlled via CORS_ALLOW_ORIGINS env var)
    @app.after_request
    def maybe_allow_cors(resp):
        allow = os.environ.get("CORS_ALLOW_ORIGINS", "")
        if allow:
            origins = [o.strip() for o in allow.split(',') if o.strip()]
            ori = request.headers.get('Origin', '')
            if ori in origins:
                resp.headers['Access-Control-Allow-Origin'] = ori
                resp.headers['Vary'] = 'Origin'
                resp.headers['Access-Control-Allow-Credentials'] = 'true'
                resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-CSRF-Token'
                resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
        return resp

    # CORS Preflight handler
    @app.route('/<path:_>', methods=['OPTIONS'])
    def _cors_preflight(_):
        return ('', 204)

    return app
