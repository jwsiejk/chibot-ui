from flask import Blueprint, request, jsonify
from services.llm_service import generate_response

chat_bp = Blueprint('chat', __name__, url_prefix='/api')

@chat_bp.route('/chat', methods=['POST'])
def chat():
    data = request.get_json(force=True, silent=True) or {}
    user_text = data.get('message') or data.get('text') or ''
    history = data.get('history') or []
    mode = data.get('mode', 'chat')  # 'chat' | 'email'
    # Simple, explicit intent guard: never call email path unless asked
    email_intent = mode == 'email' or any(
        kw in user_text.lower() for kw in ['email', 'draft email', 'send an email', 'compose email']
    )
    response = generate_response(user_text=user_text, history=history, force_email=email_intent)
    return jsonify(response), 200