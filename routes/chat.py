# routes/chat.py
from flask import Blueprint, request, jsonify, session
from services.reply_service import generate_reply
from services.entity_normalizer import detect_product, detect_intent, normalize_text_to_pure
from services.session_ctx import get as ctx_get, set as ctx_set
from utils.call_log import call_log

# Canonical blueprint (no aliases)
chat_bp = Blueprint('chat_bp', __name__, url_prefix="/api")

def _extract_text(data: dict) -> str:
    return (data.get('text')
            or data.get('message')
            or data.get('input')
            or data.get('prompt')
            or '').strip()

@chat_bp.post("/chat")
def chat():
    payload = request.get_json(silent=True) or {}
    user_text = _extract_text(payload)
    call_log.add('chat:request', 'chat', text=user_text)
    if not user_text:
        return jsonify({'ok': False, 'error': 'empty_input'}), 200

    # Load existing session context
    ctx = ctx_get(session) or {}
    prior_product = ctx.get('product') or ''
    prior_intent = ctx.get('intent') or ''

    # Detect intent/product and normalize text
    intent = detect_intent(user_text) or prior_intent
    try:
        normalized, updates = normalize_text_to_pure(user_text, preferred_product=prior_product)
    except TypeError:
        normalized, updates = normalize_text_to_pure(user_text)  # type: ignore

    detected_product = (updates or {}).get('product') or detect_product(user_text) or prior_product
    clean_text = normalized or user_text

    # Save updated context
    ctx_data = ctx_set(session, {'product': detected_product or '', 'intent': intent or ''}) or {}

    # Generate reply
    reply, err = generate_reply(clean_text, ctx=ctx_data)
    if err:
        call_log.add('warn', 'openai_error', error=err)
    call_log.add('chat:response', 'ok', size=len(reply), ctx=ctx_data)
    return jsonify({'ok': True, 'reply': reply, 'message': reply, 'text': reply})
