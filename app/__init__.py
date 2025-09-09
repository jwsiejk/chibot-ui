
# app/__init__.py
import os
from flask import Flask, Blueprint, render_template, request, session

from .api_v1 import create_v1_blueprint
from .api_v1.health import bp as health_bp
from .middleware.csrf import csrf_before_request, make_csrf_route
from .middleware.rate_limit import register_before_request as rate_limit_register

# ---------- Core blueprint (UI shells / docs) ----------
core_bp = Blueprint("core", __name__)

@core_bp.get("/")
def home():
    return render_template("index.html")

@core_bp.get("/diagnostics")
def diagnostics():
    return render_template("diagnostics.html")

@core_bp.get("/favicon.ico")
def favicon():
    # Serve from /static/favicon.ico
    from flask import current_app, send_file
    import os as _os
    return send_file(_os.path.join(current_app.static_folder, "favicon.ico"), mimetype="image/x-icon")

@core_bp.get("/logs-ui")
def logs_ui_redirect():
    # Convenience redirect for the admin log UI
    from flask import redirect
    return redirect("/api/v1/admin/logs-ui", code=302)

@core_bp.get("/admin")
def admin_console():
    # Admin-only HTML console
    from .utils.admin import is_admin_email
    from .security_state import get_user
    from flask import abort, render_template as _rt, session as _sess, request as _req
    email = (_sess.get("user") or {}).get("email") or _sess.get("email") or _req.headers.get("X-User-Email") or (get_user() or "")
    if not is_admin_email((email or "").strip().lower()):
        abort(403)
    return _rt("admin_console.html")

def create_app():
    app = Flask(
        __name__,
        static_folder="../static",
        static_url_path="/static",
        template_folder="../templates",
    )
    app.config["JSON_SORT_KEYS"] = False
    if os.environ.get("CI_FAST"):
        app.config["TESTING"] = True

    app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

    # v1-only API + core + health
    app.register_blueprint(create_v1_blueprint(), url_prefix="/api/v1")
    app.register_blueprint(core_bp)
    app.register_blueprint(health_bp)

    # Middleware
    app.before_request(csrf_before_request)
    rate_limit_register(app)

    make_csrf_route(app)

    @app.after_request
    def maybe_allow_cors(resp):
        allow = os.environ.get("CORS_ALLOW_ORIGINS", "")
        if allow:
            origins = [o.strip() for o in allow.split(",") if o.strip()]
            ori = request.headers.get("Origin", "")
            if ori in origins:
                resp.headers["Access-Control-Allow-Origin"] = ori
                resp.headers["Vary"] = "Origin"
                resp.headers["Access-Control-Allow-Credentials"] = "true"
                resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-CSRF-Token"
                resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return resp

    @app.route("/<path:_>", methods=["OPTIONS"])
    def _cors_preflight(_):
        return ("", 204)

    @app.before_request
    def _auth_gate():
        p = request.path or "/"
        allow = (
            p.startswith("/api/") or
            p.startswith("/ws") or
            p.startswith("/static") or
            p.startswith("/favicon") or
            p.startswith("/docs/") or
            p.startswith("/admin") or
            p == "/" or
            p == "/login" or
            p.startswith("/profile")
        )
        if allow:
            return
        if not (session.get("user") or {}).get("email"):
            from flask import redirect, url_for
            return redirect(url_for("core.home"))

    return app
