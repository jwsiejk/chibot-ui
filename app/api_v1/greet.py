from flask import Blueprint, jsonify, request
from ..middleware.csrf import ensure_csrf_headers
from ..db import db
from ..security_state import get_user
from ..services.streaming import make_assistant_frames, schedule_frames
try:
    from ..admin_log import emit as admin_emit
except Exception:
    def admin_emit(*args, **kwargs):  # type: ignore
        pass

bp = Blueprint("greet", __name__)

@bp.get("")
def greet():
    try:
        admin_emit('greet', msg='request')
    except Exception:
        pass

    sid = request.args.get("session_id", "default")
    email = get_user()

    # Optional profile gate
    if db.get_config().get("profile_gate_enabled") and not db.memory['profiles'].get(email):
        try:
            admin_emit('greet', msg='blocked', reason='profile_required')
        except Exception:
            pass
        return jsonify({"ok": False, "error": "profile_required"}), 400

    db.ensure_session(sid, email)
    db.add_message(sid, "system", "greet")
    try:
        tid, frames = make_assistant_frames("greet", sid)
    except Exception:
        from ..services.streaming import make_assistant_frames_text_only
        tid, frames = make_assistant_frames_text_only("greet", sid)
    schedule_frames(sid, frames)

    try:
        admin_emit('greet', msg='ok', turn_id=tid)
    except Exception:
        pass

    resp = jsonify({"ok": True, "turn_id": tid})
    return ensure_csrf_headers(resp)
