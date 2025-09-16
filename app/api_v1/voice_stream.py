# app/api_v1/voice_stream.py
from __future__ import annotations
from flask import Blueprint, request, jsonify
import asyncio
import time
import base64
import binascii

from ..services.streaming_asr.stream_manager import get_manager, asr_end

bp = Blueprint("voice_stream_v1", __name__, url_prefix="/api/v1/voice")

# simple in-proc RPS limiter per session_id
_RPS = {}  # sid -> [timestamps (sec)]
_RPS_WINDOW = 1.0
_RPS_MAX = 6
_MAX_CHUNK = 512 * 1024  # 512 KB hard guard (post-decode)

def _rps_ok(sid: str) -> bool:
    now = time.time()
    arr = _RPS.get(sid) or []
    # drop anything older than window
    arr = [t for t in arr if now - t <= _RPS_WINDOW]
    if len(arr) >= _RPS_MAX:
        _RPS[sid] = arr
        return False
    arr.append(now)
    _RPS[sid] = arr
    return True

def _get_session_id() -> str | None:
    j = request.get_json(silent=True) or {}
    return (
        request.args.get("session_id")
        or j.get("session_id")
        or j.get("sid")
        or request.form.get("session_id")
        or request.headers.get("X-Session-Id")
    )

def _read_audio_bytes() -> bytes | None:
    """
    Accept either:
      • multipart/form-data with file field 'chunk'
      • raw binary body (application/octet-stream, etc.)
      • application/json with base64 audio in 'audio_b64' / 'audio_base64' / 'b64' / 'data'
    Returns decoded bytes or None.
    """
    # 1) multipart/form-data
    f = request.files.get("chunk")
    if f:
        try:
            return f.read()
        except Exception:
            return None

    # 2) JSON with base64
    if (request.content_type or "").startswith("application/json"):
        data = request.get_json(silent=True) or {}
        b64 = (
            data.get("audio_b64")
            or data.get("audio_base64")
            or data.get("b64")
            or data.get("data")
        )
        if isinstance(b64, str) and b64:
            try:
                # validate=True raises on non-base64 chars
                return base64.b64decode(b64, validate=True)
            except (binascii.Error, ValueError):
                return None

    # 3) raw bytes body
    try:
        body = request.get_data(cache=False) or b""
        return body if body else None
    except Exception:
        return None

@bp.post("/chunk")
def voice_chunk():
    sess = _get_session_id()
    if not sess:
        return jsonify(error="missing session_id"), 400

    if not _rps_ok(sess):
        return jsonify(error="rate_limited"), 429

    data = _read_audio_bytes()
    if not data:
        return jsonify(error="missing_or_invalid_chunk"), 400

    # Enforce post-decode size guard
    if len(data) > _MAX_CHUNK:
        return jsonify(error="chunk too large", max_bytes=_MAX_CHUNK), 413

    mgr = get_manager()
    try:
        mgr.enqueue(sess, data)
    except Exception as e:
        # Keep this explicit for production diagnostics
        return jsonify(error="enqueue_failed", detail=str(e)), 500

    return jsonify(ok=True), 200

@bp.post("/end")
def voice_end():
    """Gracefully end the Deepgram ASR stream for a session (waits for final)."""
    sess = _get_session_id()
    if not sess:
        return jsonify(error="missing session_id"), 400
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(asr_end(sess, wait_for_final=True), loop)
            fut.result(timeout=10)
        else:
            loop.run_until_complete(asr_end(sess, wait_for_final=True))
    except Exception:
        # Fallback: run in a new loop; errors suppressed by design
        try:
            asyncio.run(asr_end(sess, wait_for_final=True))
        except Exception:
            pass
    return jsonify(ok=True), 200
