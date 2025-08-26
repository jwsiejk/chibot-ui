from __future__ import annotations

from flask import Blueprint, jsonify, session
import memory

try:
    from services.llm_service import generate_greeting
except Exception:
    def generate_greeting(profile=None):
        return "Hey—Chip here. What are we tackling today?"

bp = Blueprint("greet", __name__, url_prefix="/api")

@bp.get("/greet")
def api_greet():
    profile = {}
    try:
        email = session.get("email")
        if email:
            profile = memory.get_user(email) or {}
    except Exception:
        profile = {}
    text = "Hey—Chip here. What are we tackling today?"
    try:
        t = generate_greeting(profile)
        if t:
            text = str(t)
    except Exception:
        pass
    return jsonify({ "ok": True, "text": text })
