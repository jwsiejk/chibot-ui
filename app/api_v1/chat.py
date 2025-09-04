from __future__ import annotations
from flask import Blueprint, request, jsonify

bp = Blueprint("chat_v1", __name__)

@bp.post("/chat")
def chat():
    # Phase 1 will implement text turn -> LLM -> TTS stream via WS
    data = request.get_json(silent=True) or {}
    if not data.get("text"):
        return jsonify(ok=False, error="empty_text"), 400
    return jsonify(ok=False, error="not_implemented"), 501
