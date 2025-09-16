# app/api_v1/voice_stream.py
from __future__ import annotations
from flask import Blueprint, request, jsonify
import asyncio
import time

from ..services.streaming_asr.stream_manager import get_manager, asr_end

bp = Blueprint("voice_stream_v1", __name__, url_prefix="/api/v1/voice")

# simple in-proc RPS limiter per session_id
_RPS = {}  # sid -> [timestamps (sec)]
_RPS_WINDOW = 1.0
_RPS_MAX = 6

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

@bp.post("/chunk")
def voice_chunk():
    sess = request.args.get("session_id") or            (request.get_json(silent=True) or {}).get("session_id") or            request.form.get("session_id") or            request.headers.get("X-Session-Id")
    if not sess:
        return jsonify(error="missing session_id"), 400

    if not _rps_ok(sess):
        return jsonify(error="rate_limited"), 429

    f = request.files.get("chunk")
    if f:
        data = f.read()
    else:
        data = request.get_data(cache=False) or b""

    if not data:
        return jsonify(error="missing chunk"), 400
    if len(data) > 512 * 1024:
        return jsonify(error="chunk too large"), 413

    mgr = get_manager()
    try:
        mgr.enqueue(sess, data)
    except Exception as e:
        return jsonify(error="enqueue_failed", detail=str(e)), 500
    return jsonify(ok=True), 200

@bp.post("/end")
def voice_end():
    """Gracefully end the Deepgram ASR stream for a session (waits for final)."""
    sess = request.args.get("session_id") or            (request.get_json(silent=True) or {}).get("session_id") or            request.form.get("session_id") or            request.headers.get("X-Session-Id")
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
        # fallback: run in a new loop
        try:
            asyncio.run(asr_end(sess, wait_for_final=True))
        except Exception:
            pass
    return jsonify(ok=True), 200
