
# voice.py
from __future__ import annotations
import base64
from flask import Blueprint, jsonify, request
from ..services.tts_provider import get_tts_provider
from ..ws.bus import bus
from ..services.streaming import schedule_frames

bp = Blueprint("voice", __name__, url_prefix="/api/v1/voice")

@bp.post("/stt")
def stt_stub():
    # Accept file but do not process (offline/CI). Broadcast basic frames to WS bus.
    sid = request.form.get('session_id') or (request.json or {}).get('session_id') if request.is_json else None
    if sid:
        frs = [
            {"type":"text","turn_id":"stt", "text": "(mock) Transcribed audio"},
        ]
        # synthesize a short audio chunk from TTS mock for parity
        try:
            a_bytes, _ = get_tts_provider({}).synth("(mock) reply")
            import base64 as _b64
            frs.append({"type":"audio_chunk","turn_id":"stt","audio_b64": _b64.b64encode(a_bytes).decode('ascii')})
        except Exception:
            pass
        schedule_frames(sid, frs, delay_ms=10)
    return jsonify({"ok": True, "transcript": "", "is_final": True})

@bp.post("/tts-with-visemes")
def tts_with_visemes():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    # Use provider mock (offline) to synthesize
    a_bytes, vis = get_tts_provider({}).synth(text)
    audio_b64 = base64.b64encode(a_bytes).decode("ascii")
    return jsonify({"ok": True, "audio_b64": audio_b64, "visemes": vis})
