# routes/chat.py
from flask import Blueprint, request, jsonify, session
from services.reply_service import generate_reply
from utils.call_log import call_log
from services.context_guard import resolve_context

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
    
    # Context resolution (product memory + misnomer fixes)
    try:
        history = payload.get("history") or []
    except Exception:
        history = []
    session_topic = session.get("chip_topic")
    ctx = resolve_context(user_text, history=history, session_topic=session_topic)
    if ctx.get("product"):
        session["chip_topic"] = ctx["product"]
    user_text = (ctx.get("prefix") or "") + ctx.get("fixed_text", user_text)
reply, err = generate_reply(user_text)
    if err:
        call_log.add('warn', 'openai_error', error=err)
    call_log.add('chat:response', 'ask_ok', size=len(reply))
    return jsonify({'ok': True, 'message': reply})

# Multiple aliases so different frontends work without changes
_aliases = ['ask', 'chat', 'ask_chip', 'message']
for ix, path in enumerate(_aliases):
    chat_bp.add_url_rule(f'/api/{path}', endpoint=f'ask_{ix}', view_func=_ask_impl, methods=['POST'])
