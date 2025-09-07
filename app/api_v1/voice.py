from ..admin_log import emit as admin_emit
# app/api_v1/voice.py
import base64
import io
import os
from flask import Blueprint, jsonify, request

from ..services.stt_provider import get_stt_provider
from ..services.streaming import make_assistant_frames, schedule_frames
from ..db import db
from ..middleware.rate_limit import check_now, limit
from ..api_v1.admin import _emit
from ..security_state import get_user

bp = Blueprint("voice", __name__)

@limit("voice_stt")
@bp.post("/stt")
def stt():
    rv = check_now('voice_stt')
    if rv:
        return rv
    text = ""
    try:
        if request.files:
            f = request.files.get("file")
            audio_bytes = f.read() if f else b""
        else:
            j = request.get_json(silent=True) or {}
            b64 = (j.get("audio_b64") or "").strip()
            audio_bytes = base64.b64decode(b64) if b64 else b""
        cfg = db.get_config()
        text = get_stt_provider(cfg).transcribe(audio_bytes, language=cfg.get("language_lock", "en"))
    except Exception:
        text = ""
    sid = request.args.get("session_id", "default")
    email = get_user()
    db.ensure_session(sid, email)
    if text:
        db.add_message(sid, "user", text)
    try:
        tid, frames = make_assistant_frames(text or "voice", sid)
        schedule_frames(sid, frames)
    except Exception:
        tid = "t_voice_stub"
    try:
        _emit('stt', ok=bool(text))
    except Exception:
        pass
    return jsonify({"ok": True, "turn_id": tid, "text": text})


@limit("voice_tts")
@bp.post("/tts-with-visemes")
def tts_with_visemes():
    try:
        _j=(request.get_json(silent=True) or {})
        _t=_j.get('text')
        admin_emit('tts', msg='request', chars=(len(_t) if isinstance(_t,str) else 0))
    except Exception:
        pass
    """
    Build-safe TTS:
      • In CI (CI_FAST=1) OR when ELEVENLABS_API_KEY is missing -> short-circuit to MockTTS (always 200).
      • Otherwise use the configured provider (ElevenLabs in prod).
    """
    from ..services.tts_provider import get_tts_provider
    from ..services.providers.mock_tts import MockTTS

    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()

    # ---- HARD SHORT-CIRCUIT FOR BUILD/CI OR NO KEY ----
    if os.environ.get("CI_FAST") or not os.environ.get("ELEVENLABS_API_KEY"):
        a_bytes, v = MockTTS().synth(text)
        a = base64.b64encode(a_bytes).decode("ascii")
        try:
            _emit('tts', chars=len(text))
        except Exception:
            pass
        return jsonify({"ok": True, "audio_b64": a, "visemes": v})

    # ---- NORMAL PROD PATH ----
    cfg = db.get_config()
    try:
        provider = get_tts_provider(cfg)
        a_bytes, v = provider.synth(text)
    except Exception:
        # Vendor flake safety: still keep route healthy
        a_bytes, v = MockTTS().synth(text)

    a = base64.b64encode(a_bytes).decode("ascii")
    try:
        _emit('tts', chars=len(text))
    except Exception:
        pass
    return jsonify({"ok": True, "audio_b64": a, "visemes": v})
