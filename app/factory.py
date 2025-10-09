from __future__ import annotations
from flask import Flask
from app.middleware.errors import register_error_handlers
from app.api_v1 import create_v1_blueprint

def create_app() -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="../templates")
    # Minimal secret for session; real value comes from env in production
    app.config.setdefault("SECRET_KEY", "dev-secret-change-me")
    # Blueprints (v1-only surfaces)
    app.register_blueprint(create_v1_blueprint(), url_prefix="/api/v1")
    # Errors
    register_error_handlers(app)
    return app
