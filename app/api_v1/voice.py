import base64
import io
from flask import Blueprint, jsonify, request
from ..services.stt_provider import get_stt_provider
from ..services.streaming import make_assistant_frames, schedule_frames
from ..db import db
from ..middleware.rate_limit import check_now
from ..middleware.rate_limit import limit
from ..api_v1.admin import _emit
from ..security_state import get_user
bp = Blueprint("voice", __name__)
@limit("voice_stt")
@bp.post("/stt")
def stt():
    rv = check_now('voice_stt')
    if rv: return rv
    if 'file' not in request.files: return jsonify({"ok": False, "error":"missing_file"}), 400
    f=request.files['file']; audio=f.read()
    sid=request.form.get('session_id') or 'default'
    text=transcribe(audio, request.form.get('mime') or 'audio/webm', request.form.get('meta') or '{}')
    try:
        _emit('stt', bytes=len(audio))
    except Exception:
        pass
    email=get_user(); db.ensure_session(sid, email); db.add_message(sid,"user",text)
    tid, frames = make_assistant_frames(text or "voice", sid); schedule_frames(sid, frames)
    try:
        from ..policy.nudges import cancel_nudge
        cancel_nudge(sid)
    except Exception:
        pass
    return jsonify({"ok": True, "turn_id": tid, "text": text})
@limit("voice_tts")
@bp.post("/tts-with-visemes")
def tts_with_visemes():
    from ..services.tts_provider import get_tts_provider
    data=request.get_json(silent=True) or {}; text=(data.get("text") or "").strip()
    cfg=db.get_config(); a_bytes, v = get_tts_provider(cfg).synth(text); a = base64.b64encode(a_bytes).decode('ascii')
    try:
        _emit('tts', chars=len(text))
    except Exception:
        pass
    return jsonify({"ok": True, "audio_b64": a, "visemes": v})
