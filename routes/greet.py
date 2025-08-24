from __future__ import annotations
from flask import Blueprint, jsonify, session
import memory
from services.llm_service import generate_greeting

bp = Blueprint("greet", __name__, url_prefix="/api")

@bp.route("/greet", methods=["GET"])
def greet_api():
    email = session.get("email")
    profile = memory.get_user(email) if email else {}
    text = generate_greeting(profile or {})
    return jsonify({"ok": True, "text": text})
