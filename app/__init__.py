# app/__init__.py
import os
from flask import Flask, render_template, send_from_directory, request
from .api_v1 import create_v1_blueprint
from .middleware.csrf import csrf_before_request
from .middleware.rate_limit import register_before_request as rate_limit_register

def create_app():
    # Serve /static (JS/CSS/img) and /templates
    app = Flask(__name__, static_folder='../static', static_url_path='/static', template_folder='../templates')
    app.config['JSON_SORT_KEYS'] = False

    # Secret key (read env in production)
    app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

    # Register v1-only API
    bp = create_v1_blueprint()
    app.register_blueprint(bp, url_prefix="/api/v1")

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

    # Security headers & CORS
    @app.after_request
    def _security_headers(resp):
        # Security headers
        resp.headers['X-Content-Type-Options'] = 'nosniff'
        resp.headers['X-Frame-Options'] = 'DENY'
        resp.headers['Referrer-Policy'] = 'no-referrer'
        # CSP tuned for self-hosted assets and WSS
        resp.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self' wss:; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'"
        # HSTS (enable only on HTTPS)
        if request.scheme == 'https' or request.headers.get('X-Forwarded-Proto','') == 'https':
            resp.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains; preload'
        # CORS allowlist (off by default)
        allow = os.environ.get('CORS_ALLOW_ORIGINS','').strip()
        if allow:
            origins = [o.strip() for o in allow.split(',') if o.strip()]
            ori = request.headers.get('Origin','')
            if ori in origins:
                resp.headers['Access-Control-Allow-Origin'] = ori
                resp.headers['Vary'] = 'Origin'
                resp.headers['Access-Control-Allow-Credentials'] = 'true'
                resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-CSRF-Token'
                resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
        return resp

    @app.route('/<path:_>',
               methods=['OPTIONS'])
    def _cors_preflight(_):
        return ('', 204)


# Serve markdown docs
from flask import send_from_directory
import os as _os
_docs_dir = _os.path.join(app.root_path, "..", "docs")
@_app.route if False else app.get  # quiet lint
def _noop(): pass
@app.get("/docs/<path:fname>")
def serve_docs(fname):
    return send_from_directory(_docs_dir, fname)

    return app
