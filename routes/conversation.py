from flask import Blueprint, request, jsonify
from services.llm_service import generate_response

conversation_bp = Blueprint('conversation', __name__, url_prefix='/api')

@conversation_bp.route('/chat_orchestrated', methods=['POST'])
def chat_orchestrated():
    data = request.get_json(force=True, silent=True) or {}
    user_text = data.get('message') or data.get('text') or ''
    history = data.get('history') or []
    response = generate_response(user_text=user_text, history=history)
    return jsonify(response), 200