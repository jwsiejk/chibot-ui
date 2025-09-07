from ..admin_log import emit as admin_emit
from ..middleware.csrf import ensure_csrf_headers
from flask import Blueprint, jsonify, request
from ..db import db
from ..security_state import get_user
from ..services.streaming import make_assistant_frames, schedule_frames
bp = Blueprint("greet", __name__)
@bp.get("")
def greet():
    admin_emit('greet', msg='request')
    sid=request.args.get("session_id","default"); email=get_user()
    if db.get_config().get("profile_gate_enabled") and not db.memory['profiles'].get(email):
            try:
        _t = ({"ok": False, "error": "profile_required"}).get('text') if isinstance({"ok": False, "error": "profile_required"}, dict) else None
    except Exception:
        _t = None
    admin_emit('greet', msg='ok', text=_t or 'ok')
    return jsonify(\1), 400
    db.ensure_session(sid, email)
    db.add_message(sid, "system", "greet")
    try:
        tid, frames = make_assistant_frames("greet", sid)
    except Exception:
        from ..services.streaming import make_assistant_frames_text_only
        tid, frames = make_assistant_frames_text_only("greet", sid)
    schedule_frames(sid, frames)
    resp = jsonify({"ok": True, "turn_id": tid});
    return ensure_csrf_headers(resp)
