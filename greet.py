from flask import Blueprint, request, jsonify, session
from services.llm_service import generate_greeting
import memory

bp = Blueprint("greet", __name__)

@bp.route("/api/greet", methods=["GET", "POST"])
def api_greet():
    email = None
    try:
        email = session.get("email")
    except Exception:
        email = None

    profile = None
    try:
        if email:
            user = memory.get_user(email)
            if isinstance(user, dict):
                profile = {
                    "email": email,
                    "name": user.get("name"),
                    "title": user.get("title"),
                    "region": user.get("region"),
                }
    except Exception:
        profile = None

    try:
        text = generate_greeting(profile=profile)
        return jsonify(ok=True, text=text)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500
