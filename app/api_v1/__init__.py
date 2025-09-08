from flask import Blueprint
from .admin import bp as admin_bp
from .auth import bp as auth_bp
from .profile import bp as profile_bp
from .greet import bp as greet_bp
from .chat import bp as chat_bp
from .voice import bp as voice_bp

def create_v1_blueprint():
    # v1 routes
    bp = Blueprint("api_v1", __name__)
    bp.register_blueprint(admin_bp, url_prefix="/admin")
    bp.register_blueprint(auth_bp, url_prefix="/auth")
    bp.register_blueprint(profile_bp, url_prefix="/profile")
    bp.register_blueprint(greet_bp, url_prefix="/greet")
    bp.register_blueprint(chat_bp, url_prefix="/chat")
    bp.register_blueprint(voice_bp, url_prefix="/voice")
    return bp

# auto-registered by patch
from .profile import bp as profile_v1
