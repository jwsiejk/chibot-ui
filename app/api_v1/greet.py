from ..middleware.csrf import ensure_csrf_headers
from flask import Blueprint, jsonify, request
from ..db import db
from ..security_state import get_user
from ..services.streaming import make_assistant_frames, schedule_frames
try:
    from ..api_v1.admin import _emit
except Exception:
    def _emit(*a, **k):
        pass
bp = Blueprint("greet", __name__)
@bp.get("")
def greet():
    try:
        _emit('start', msg='test run started', mode='chat', settings=db.get_config(), label='start: test run started')
    except Exception:
        pass
    sid=request.args.get("session_id","default"); email=get_user()
    if db.get_config().get("profile_gate_enabled") and not db.memory['profiles'].get(email):
        return jsonify({"ok": False, "error": "profile_required"}), 400
    db.ensure_session(sid, email)
    db.add_message(sid, "system", "greet")
    try:
        _emit('greet:req', label="greet:req – make_assistant_frames('greet')", route="/api/v1/greet")
    except Exception:
        pass
    try:
        tid, frames = make_assistant_frames("greet", sid)
        try:
            _emit('greet:ok', label='greet:ok – frames ready', turn_id=tid, n=len(frames))
        except Exception:
            pass
    except Exception:
        from ..services.streaming import make_assistant_frames_text_only
        tid, frames = make_assistant_frames_text_only("greet", sid)
    schedule_frames(sid, frames, enable_nudge=False)
    try:
        _emit('greet:audio', label='greet:audio – summary', audio_chunks=0, total_bytes=0, viseme_sets=0)
    except Exception:
        pass
    resp = jsonify({"ok": True, "turn_id": tid});
    return ensure_csrf_headers(resp)
