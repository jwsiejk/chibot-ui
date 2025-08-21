# server/__init__.py
import logging
from flask import Flask

log = logging.getLogger(__name__)

def create_app():
    """
    Application factory. Creates the Flask app and registers blueprints AFTER
    the app object exists to avoid NameError on 'app'.
    """
    app = Flask(__name__)

    # Register blueprints strictly after app is created.
    # Try common locations for greet without assuming a specific project layout.
    try:
        try:
            # e.g., server/routes/greet.py exporting `bp`
            from .routes.greet import bp as greet_bp
        except Exception:
            try:
                # e.g., server/greet.py exporting `bp`
                from .greet import bp as greet_bp
            except Exception:
                # e.g., project_root/greet.py exporting `bp`
                from greet import bp as greet_bp

        # IMPORTANT: do NOT add a url_prefix here because greet.py already
        # declares the route as "/api/greet". Adding a prefix would double it.
        app.register_blueprint(greet_bp)
        app.logger.info("Registered greet blueprint at /api/greet")

    except Exception:
        # Use stdlib logger in failure path to avoid referencing app prematurely.
        log.exception("Failed to register greet blueprint")

    return app
