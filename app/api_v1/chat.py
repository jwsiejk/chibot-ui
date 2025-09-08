import base64
from flask import Blueprint, jsonify, request
from ..db import db
from ..security_state import get_user
from ..services.mailer import send_transcript
from ..services.streaming import make_assistant_frames, schedule_frames
from ..middleware.rate_limit import limit, check_now
from ..ws.bus import bus
from ..admin_log import emit as _admin_emit
bp = Blueprint("chat", __name__)
_TTS_MEMO = {}
@bp.before_request
def _chat_rl_guard():
    # rate limit guard
    
    rv = check_now('chat')
    return rv

@limit("chat")
@bp.post("")
def chat():
    # auth/profile guard
    email = get_user()
    if not email:
        return jsonify({"ok": False, "error": "not_authenticated"}), 401

    data=request.get_json(silent=True) or {}
    sid=data.get("session_id","default"); cmd=data.get("cmd"); email=get_user()
    if cmd=="interrupt":
        tid=data.get("turn_id"); bus.cancel_turn(sid, tid); bus.broadcast(sid, {"type":"state","phase":"ready"}); return jsonify({"ok": True, "interrupted": True})
    if cmd=="nudge":
        s = db.memory['sessions'].setdefault(sid, {'email': email, 'messages': [], 'nudges': 0, 'persona_id':'chip'}); s['nudges']+=1
        if s['nudges']<=2:
            tid, frames = make_assistant_frames("Still with me? Want a quick recap?")
            for fr in frames:
                if fr.get("type")=="end": fr["reason"]="nudge"
            schedule_frames(sid, frames); return jsonify({"ok": True, "nudged": True, "count": s['nudges']})
        return jsonify({"ok": True, "nudged": False, "count": s['nudges']})
    if cmd=="end_session":
        body=db.get_transcript(sid); send_transcript(email,"Ask Chip — Session transcript",body); return jsonify({"ok": True, "emailed": True})
    text=(data.get("text") or "").strip()
    if text:
        db.ensure_session(sid, email)
        db.add_message(sid, "user", text)
        try:
            from ..policy.nudges import cancel_nudge
            cancel_nudge(sid)
        except Exception:
            pass
    tid, frames = make_assistant_frames((text or "chat"), sid); schedule_frames(sid, frames)
    return jsonify({"ok": True, "turn_id": tid})


@limit("voice_tts")
@bp.post("/tts-with-visemes")
def tts_with_visemes():
    from ..services.tts_provider import get_tts_provider
    data=request.get_json(silent=True) or {}; text=(data.get("text") or "").strip()
    cfg = db.get_config()
    if text in _TTS_MEMO:
        a, v = _TTS_MEMO[text]
    else:
        a_bytes, v = get_tts_provider(cfg).synth(text)
        a = base64.b64encode(a_bytes).decode("ascii")
        _TTS_MEMO[text] = (a, v)
    try:
        from ..api_v1.admin import _emit
        _emit('tts', chars=len(text))
    except Exception:
        pass
    return jsonify({"ok": True, "audio_b64": a, "visemes": v})
@bp.before_request
def _diag_chat_before():
    # Only trace this blueprint; keep payloads short to avoid PII in logs
    try:
        if request.method == "POST":
            j = request.get_json(silent=True)
            _admin_emit("chat:request", path=request.path, len=(len(json.dumps(j)) if j else 0))
    except Exception:
        pass

@bp.after_request
def _diag_chat_after(resp):
    try:
        _admin_emit("chat:response", path=request.path, status=getattr(resp, "status_code", None))
    except Exception:
        pass
    return resp

@bp.errorhandler(Exception)
def _diag_chat_error(e):
    try:
        _admin_emit("chat:error", path=request.path, error=str(e.__class__.__name__), msg=str(e))
    except Exception:
        pass
    # Preserve original behavior but ensure JSON error is sent
    from flask import jsonify
    return jsonify({"ok": False, "error": "chat_handler_failed", "detail": str(e)}), 500
