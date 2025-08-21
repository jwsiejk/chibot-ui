# server/__init__.py
import os
import logging
from flask import Flask, jsonify, make_response, send_from_directory, abort

log = logging.getLogger(__name__)

def _detect_ui_root():
    """
    Try to find a built UI to serve. First honors UI_ROOT env var (relative or absolute),
    then checks common folders at the repo root. Returns an absolute path or None.
    """
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    candidates = []

    # 1) Explicit env override (recommended if your UI lives in a custom folder)
    env_ui = os.getenv("UI_ROOT")
    if env_ui:
        # Resolve relative to repo root if not absolute
        env_path = env_ui if os.path.isabs(env_ui) else os.path.join(base_dir, env_ui)
        candidates.append(env_path)

    # 2) Common locations (adjustable later without breaking anything)
    candidates += [
        os.path.join(base_dir, "public"),
        os.path.join(base_dir, "static"),
        os.path.join(base_dir, "build"),
        os.path.join(base_dir, "dist"),
        os.path.join(base_dir, "frontend", "build"),
        os.path.join(base_dir, "frontend", "dist"),
        os.path.join(base_dir, "ui", "build"),
        os.path.join(base_dir, "ui", "dist"),
        base_dir,  # in case index.html is at repo root
    ]

    for p in candidates:
        try:
            if p and os.path.exists(os.path.join(p, "index.html")):
                return os.path.abspath(p)
        except Exception:
            continue
    return None

def create_app():
    """
    Application factory. Creates the Flask app and registers blueprints AFTER
    the app object exists to avoid NameError on 'app'.
    """
    app = Flask(__name__)

    # ----------------------------------------------------------------------
    # Register blueprints strictly *after* app is created.
    # Keep greet at /api/greet exactly as your current greet.py defines it.
    # ----------------------------------------------------------------------
    try:
        try:
            # e.g., server/routes/greet.py exporting `bp`
            from .routes.greet import bp as greet_bp
        except Exception:
            try:
                # e.g., server/greet.py exporting `bp`
                from .greet import bp as greet_bp
            except Exception:
                # e.g., repo_root/greet.py exporting `bp`
                from greet import bp as greet_bp

        app.register_blueprint(greet_bp)  # greet.py already mounts "/api/greet"
        try:
            app.logger.info("Registered greet blueprint at /api/greet")
        except Exception:
            log.info("Registered greet blueprint at /api/greet")
    except Exception:
        # Use stdlib logger in failure path to avoid referencing app prematurely.
        log.exception("Failed to register greet blueprint")

    # ----------------------------------------------------------------------
    # UI / root handling
    # If an index.html is present, serve it at "/", plus any static assets.
    # Otherwise, return the same JSON health payload you saw (no downtime).
    # ----------------------------------------------------------------------
    ui_root = _detect_ui_root()

    if ui_root:
        @app.route("/", defaults={"path": ""})
        @app.route("/<path:path>")
        def serve_ui(path):
            # Let API routes be handled by their blueprints
            if path.startswith("api/"):
                abort(404)

            # Serve a concrete file if it exists (e.g., /assets/app.js)
            full = os.path.join(ui_root, path)
            if path and os.path.isfile(full):
                return send_from_directory(ui_root, path)

            # Otherwise, serve the SPA entrypoint (supports client-side routing)
            return send_from_directory(ui_root, "index.html")

        @app.route("/favicon.ico")
        def favicon():
            # Serve a real favicon if present; else return 204 (no content)
            for name in ("favicon.ico", "favicon.png"):
                fp = os.path.join(ui_root, name)
                if os.path.isfile(fp):
                    return send_from_directory(ui_root, name)
            return make_response(b"", 204)

    else:
        # Fallback health/landing payload (only if no UI present)
        @app.get("/")
        def root():
            return jsonify(ok=True, service="ask-chip", endpoints=["/api/greet"])

        @app.get("/favicon.ico")
        def favicon():
            return make_response(b"", 204)

    return app
