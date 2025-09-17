
# voice.py
from __future__ import annotations
import base64
from flask import Blueprint, jsonify, request
from ..services.tts_provider import get_tts_provider

bp = Blueprint("voice", __name__, url_prefix="/api/v1/voice")

@bp.post("/stt")
def stt_stub():
    # WS-only migration: HTTP STT is a stub for presence only (tests assert route presence)
    # Accepts no audio; returns ok with empty transcript.
    return jsonify({"ok": True, "transcript": "", "is_final": True})

@bp.post("/tts-with-visemes")
def tts_with_visemes():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    # Use provider mock (offline) to synthesize
    a_bytes, vis = get_tts_provider({}).synth(text)
    audio_b64 = base64.b64encode(a_bytes).decode("ascii")
    return jsonify({"ok": True, "audio_b64": audio_b64, "visemes": vis})
