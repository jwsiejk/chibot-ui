
# --- BEGIN: assistant patch (orchestrator aliases & robust parsing) ---
@conversation_bp.route('/orchestrator', methods=['POST', 'OPTIONS'])
@conversation_bp.route('/orchestrate', methods=['POST', 'OPTIONS'])
@conversation_bp.route('/conversation', methods=['POST', 'OPTIONS'])
def chat_orchestrator_alias():
    if request.method == 'OPTIONS':
        # Let Flask-CORS/after_request add headers; return 204 quickly
        return ('', 204)
    data = request.get_json(force=True, silent=True) or {}
    user_text = (data.get('message') or data.get('text') or data.get('prompt') or '').strip()
    history = data.get('history') or data.get('messages') or []
    if not user_text:
        return jsonify({"ok": False, "error": "Prompt required"}), 400
    try:
        resp = generate_response(user_text=user_text, history=history)
        # normalize output
        if isinstance(resp, dict) and 'text' in resp:
            return jsonify({"ok": True, **resp}), 200
        if isinstance(resp, str):
            return jsonify({"ok": True, "text": resp}), 200
        return jsonify({"ok": True, "text": str(resp)}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": f"orchestrator failed: {e}"}), 500
# --- END: assistant patch ---
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