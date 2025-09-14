
# app/api_v1/voice.py — Phase 0 migration to /api/v1/voice/chunk
from __future__ import annotations
import base64
from flask import Blueprint, jsonify, request
from ..middleware.rate_limit import check_now, limit
from ..ws.bus import bus
from ..services.streaming_asr.stream_manager import get_manager

bp = Blueprint("voice", __name__)

@bp.before_request
def _voice_rl_guard():
    rv = check_now('voice_chunk')
    if rv is not None:
        return rv

def _session_id() -> str:
    sid = (request.args.get("session_id") or request.headers.get("X-Session-Id") or "").strip()
    return sid or "default"

@bp.post("/chunk")
@limit('voice_chunk')
def voice_chunk():
    data = request.get_json(silent=True) or {}
    user_msg_id = (data.get("user_msg_id") or "").strip()
    chunk_seq = data.get("chunk_seq")
    audio_b64 = data.get("audio_b64")
    fmt = (data.get("format") or "webm-opus").strip().lower()
    if not user_msg_id or not isinstance(chunk_seq, int) or audio_b64 is None:
        return jsonify(ok=False, error="bad_request", detail="Expected user_msg_id:str, chunk_seq:int, audio_b64:str"), 400
    # Validate base64
    try:
        _ = base64.b64decode(audio_b64, validate=True)
    except Exception:
        return jsonify(ok=False, error="bad_request", detail="audio_b64 must be valid base64"), 400

    sid = _session_id()
    frame = {
        "type": "voice_chunk",
        "session_id": sid,
        "user_msg_id": user_msg_id,
        "chunk_seq": int(chunk_seq),
        "format": fmt,
        "base64": audio_b64,
    }
    # Publish to the WS bus for ASR/Orchestrator; ASR hookup lands in later phases
    bus.broadcast(sid, frame)
    try:
        mgr = get_manager()
        if isinstance(audio_b64, str) and audio_b64:
            import base64 as _b64
            _data = _b64.b64decode(audio_b64.encode('ascii'), validate=False)
        else:
            _data = b''
        if _data:
            mgr.enqueue(sid, _data)
    except Exception:
        pass
    return jsonify(ok=True, received_seq=int(chunk_seq))

# ---------- Legacy endpoints: hard 410 (gone) ----------

@bp.post("/stt")
def legacy_stt():
    return jsonify(ok=False, error="gone", replacement="/api/v1/voice/chunk"), 410

@bp.post("/tts-with-visemes")
def legacy_tts():
    return jsonify(ok=False, error="gone", replacement="/api/v1/voice/chunk"), 410
