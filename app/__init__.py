# app/__init__.py
import os
from flask import Flask, Blueprint, render_template, send_from_directory, request, session

from .api_v1 import create_v1_blueprint
from .middleware.csrf import csrf_before_request, make_csrf_route
from .middleware.rate_limit import register_before_request as rate_limit_register

# ---------- Core blueprint (UI shells / docs) ----------
core_bp = Blueprint("core", __name__)

@core_bp.get("/")
def home():
    # Main Ask Chip UI; login/profile are handled with inline modals by the client.
    return render_template("index.html")

@core_bp.get("/login")
def login_page():
    # Present but not used in normal flow (kept only for safety).
    return render_template("login.html")

@core_bp.get("/profile")
def profile_page():
    # Menu access to view/edit profile (first-time gating uses inline modal).
    return render_template("profile.html")

# Optional small helpers (safe to keep here)
@core_bp.get("/diagnostics")
def diagnostics():
    return render_template("diagnostics.html")

@core_bp.get("/favicon.ico")
def favicon():
    return send_from_directory("static", "favicon.ico", mimetype="image/x-icon")

def create_app():
    # Serve /static (JS/CSS/img) and /templates
    app = Flask(
        __name__,
        static_folder="../static",
        static_url_path="/static",
        template_folder="../templates",
    )
    app.config["JSON_SORT_KEYS"] = False
    if os.environ.get("CI_FAST"):
        app.config["TESTING"] = True

    # Secret key for session cookies (must be set in prod)
    app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

    # ---------- Register v1 API + core ----------
    app.register_blueprint(create_v1_blueprint(), url_prefix="/api/v1")
    app.register_blueprint(core_bp)

    # ---------- Middleware ----------
    app.before_request(csrf_before_request)
    rate_limit_register(app)

    # Issues CSRF token and sets session value; client reads header X-CSRF-Token
    make_csrf_route(app)

    # Simple CORS (optional; set CORS_ALLOW_ORIGINS to enable)
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

    # Cache-buster for static
    @app.context_processor
    def inject_asset_version():
        return {
            "asset_version": os.environ.get("ASSET_VERSION")
                            or os.environ.get("RELEASE_VERSION")
                            or "v" + os.getenv("BUILD_VERSION", "dev")
        }

    # CORS preflight
    @app.route("/<path:_>", methods=["OPTIONS"])
    def _cors_preflight(_):
        return ("", 204)

    # ---------- Auth gate ----------
    # Allow "/" so the UI can render and show inline login/profile modals.
    # Never bounce to /login for these; let the client control modals.
    @app.before_request
    def _auth_gate():
        p = request.path or "/"
        allow = (
            p.startswith("/api/") or
            p.startswith("/ws") or
            p.startswith("/static") or
            p.startswith("/favicon") or
            p.startswith("/docs/") or
            p == "/" or
            p == "/login" or
            p.startswith("/profile")
        )
        if allow:
            return
        if not (session.get("user") or {}).get("email"):
            from flask import redirect, url_for
            # Redirect to "/" (not /login); the client will show the login modal
            return redirect(url_for("core.home"))

    return app
