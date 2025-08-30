# routes/chat.py
from flask import Blueprint, request, jsonify, session
from services.reply_service import generate_reply
from services.entity_normalizer import detect_product, detect_intent, normalize_text_to_pure
from services.asr_normalizer import normalize_asr
from services.session_ctx import get as ctx_get, set as ctx_set
from utils.call_log import call_log

chat_bp = Blueprint('chat_bp', __name__)

def _extract_text(data: dict) -> str:
    return (data.get('text')
            or data.get('message')
            or data.get('input')
            or data.get('prompt')
            or '').strip()

def _ask_impl():
    payload = request.get_json(silent=True) or {}
    user_text = _extract_text(payload)
    call_log.add('chat:request', 'ask', text=user_text)
    if not user_text:
        return jsonify({'ok': False, 'error': 'empty_input'}), 200

    # Load existing session context
    ctx = ctx_get(session)
    prior_product = ctx.get('product') if isinstance(ctx, dict) else ''
    prior_intent = ctx.get('intent') if isinstance(ctx, dict) else ''

    # Detect user intent (e.g., install/config/troubleshoot) and product
    intent = detect_intent(user_text) or prior_intent
    # Normalize text toward Pure terms; also returns any inferred updates
    try:
        normalized, updates = normalize_text_to_pure(user_text, preferred_product=prior_product)
    except TypeError:
        # Fallback if function signature is normalize_text_to_pure(text) -> (text, updates)
        normalized, updates = normalize_text_to_pure(user_text)  # type: ignore

    detected_product = (
        (updates or {}).get('product')
        or detect_product(user_text)
        or prior_product
    )

    clean_text = normalized or user_text

    # Save updated context back to the session
    ctx_data = ctx_set(session, {'product': detected_product or '', 'intent': intent or ''})

    # Generate reply with context hinting
    reply, err = generate_reply(clean_text, ctx=ctx_data)
    if err:
        call_log.add('warn', 'openai_error', error=err)
    call_log.add('chat:response', 'ask_ok', size=len(reply), ctx=ctx_data)
    return jsonify({'ok': True, 'reply': reply, 'message': reply, 'text': reply})

# Multiple aliases so different frontends work without changes
_aliases = ['ask', 'chat', 'ask_chip', 'message']
for ix, path in enumerate(_aliases):
    chat_bp.add_url_rule(f'/api/{path}', endpoint=f'ask_{ix}', view_func=_ask_impl, methods=['POST'])
