import io
from flask import Blueprint, jsonify, request
from ..services.mock_stt import transcribe
from ..services.streaming import make_assistant_frames, schedule_frames
from ..db import db
from ..security_state import get_user
bp = Blueprint("voice", __name__)
@bp.post("/stt")
def stt():
    if 'file' not in request.files: return jsonify({"ok": False, "error":"missing_file"}), 400
    f=request.files['file']; audio=f.read()
    sid=request.form.get('session_id') or 'default'
    text=transcribe(audio, request.form.get('mime') or 'audio/webm', request.form.get('meta') or '{}')
    email=get_user(); db.ensure_session(sid, email); db.add_message(sid,"user",text)
    tid, frames = make_assistant_frames(text or "voice"); schedule_frames(sid, frames)
    return jsonify({"ok": True, "turn_id": tid, "text": text})
@bp.post("/tts-with-visemes")
def tts_with_visemes():
    from ..services.mock_tts import synth
    data=request.get_json(silent=True) or {}; text=(data.get("text") or "").strip()
    a,v=synth(text); return jsonify({"ok": True, "audio_b64": a, "visemes": v})
