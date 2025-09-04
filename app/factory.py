from __future__ import annotations
from flask import Flask
from app.middleware.errors import register_error_handlers
from app.api_v1.greet import bp as bp_greet
from app.api_v1.chat import bp as bp_chat
from app.api_v1.voice import bp as bp_voice
from app.api_v1.admin import bp as bp_admin

def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)
    # Minimal secret for session; real value comes from env in production
    app.config.setdefault("SECRET_KEY", "dev-secret-change-me")
    # Blueprints (v1-only surfaces)
    app.register_blueprint(bp_greet, url_prefix="/api/v1")
    app.register_blueprint(bp_chat, url_prefix="/api/v1")
    app.register_blueprint(bp_voice, url_prefix="/api/v1")
    app.register_blueprint(bp_admin, url_prefix="/api/v1")
    # Errors
    register_error_handlers(app)
    return app
