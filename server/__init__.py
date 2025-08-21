# server/__init__.py
import os
import logging
from flask import Flask, jsonify, make_response, send_from_directory, abort

log = logging.getLogger(__name__)

def _abs(base: str, path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(base, path)

def _detect_ui_root() -> str | None:
    """
    Find the folder that contains the UI's index.html.
    Priority:
      1) UI_ROOT env var (absolute or relative to repo root)
      2) Common build/static and template locations
      3) Repo root (if index.html is there)
    Returns absolute path or None if not found.
    """
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    candidates: list[str] = []

    # 1) Explicit override
    env_ui = os.getenv("UI_ROOT")
    if env_ui:
        candidates.append(_abs(base_dir, env_ui))

    # 2) Common places in typical repos
    candidates += [
        os.path.join(base_dir, "public"),
        os.path.join(base_dir, "static"),
        os.path.join(base_dir, "build"),
        os.path.join(base_dir, "dist"),
        os.path.join(base_dir, "client", "build"),
        os.path.join(base_dir, "client", "dist"),
        os.path.join(base_dir, "frontend", "build"),
        os.path.join(base_dir, "frontend", "dist"),
        os.path.join(base_dir, "ui", "build"),
        os.path.join(base_dir, "ui", "dist"),
        # Template-driven locations (serve as static to avoid templating changes)
        os.path.join(base_dir, "templates"),
        os.path.join(base_dir, "server", "templates"),
        os.path.join(base_dir, "server", "static"),
        # 3) Root
        base_dir,
    ]

    for p in candidates:
        try:
            if os.path.isfile(os.path.join(p, "index.html")):
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

    # ------------------------------------------------------------------
    # Register greet blueprint AFTER app exists (no change to /api/greet)
    # ------------------------------------------------------------------
    try:
        try:
            from .routes.greet import bp as greet_bp
        except Exception:
            try:
                from .greet import bp as greet_bp
            except Exception:
                from greet import bp as greet_bp
        app.register_blueprint(greet_bp)  # greet.py already mounts "/api/greet"
        try:
            app.logger.info("Registered greet blueprint at /api/greet")
        except Exception:
            log.info("Registered greet blueprint at /api/greet")
    except Exception:
        log.exception("Failed to register greet blueprint")

    # ------------------------------------------------------------------
    # UI / root handling
    # ------------------------------------------------------------------
    ui_root = _detect_ui_root()

    if ui_root:
        @app.route("/", defaults={"path": ""})
        @app.route("/<path:path>")
        def serve_ui(path: str):
            # Let API paths be handled by their blueprints
            if path.startswith("api/"):
                abort(404)

            # Serve a concrete asset if it exists (e.g., /assets/app.js)
            full = os.path.join(ui_root, path)
            if path and os.path.isfile(full):
                return send_from_directory(ui_root, path)

            # Otherwise serve SPA entry (supports client-side routing)
            return send_from_directory(ui_root, "index.html")

        @app.route("/favicon.ico")
        def favicon():
            # Serve a real favicon if present; else be quiet with 204
            for name in ("favicon.ico", "favicon.png"):
                fp = os.path.join(ui_root, name)
                if os.path.isfile(fp):
                    return send_from_directory(ui_root, name)
            return make_response(b"", 204)
    else:
        # Fallback health payload ONLY if no UI index.html is found anywhere.
        @app.get("/")
        def root():
            return jsonify(ok=True, service="ask-chip", endpoints=["/api/greet"])

        @app.get("/favicon.ico")
        def favicon():
            return make_response(b"", 204)

    return app
