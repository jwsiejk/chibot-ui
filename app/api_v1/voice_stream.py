# app/api_v1/voice_stream.py
from __future__ import annotations
from flask import Blueprint, request, jsonify
import asyncio
import time

from ..services.streaming_asr.stream_manager import get_manager

bp = Blueprint("voice_stream_v1", __name__, url_prefix="/api/v1/voice")

# simple in-proc RPS limiter per session_id
_RPS = {}  # sid -> [timestamps (sec)]
_RPS_WINDOW = 1.0
_RPS_MAX = 6

def _rps_ok(sid: str) -> bool:
    now = time.time()
    lst = _RPS.setdefault(sid, [])
    # keep only last 1s
    while lst and (now - lst[0]) > _RPS_WINDOW:
        lst.pop(0)
    if len(lst) >= _RPS_MAX:
        return False
    lst.append(now)
    return True

@bp.post("/stt/stream")
def voice_stt_stream():
    sess = request.args.get("session_id") or request.form.get("session_id") or "default"

    # circuit breaker awareness (manager drops enqueues if open; we surface 503)
    from ..services.streaming_asr.stream_manager import _cb_opened
    if _cb_opened():
        return jsonify(error="provider_unavailable", retry_after=60), 503

    if not _rps_ok(sess):
        return jsonify(error="rate_limited", max_per_sec=_RPS_MAX), 429

    # Accept either multipart/form-data (chunk) or raw octet-stream
    data = b""
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
    mgr.enqueue(sess, data)
    return jsonify(ok=True), 200


@bp.post("/end")
def voice_end():
    """Gracefully end the Deepgram ASR stream for a session (waits for final)."""
    sess = request.args.get("session_id") or \
           (request.get_json(silent=True) or {}).get("session_id") or \
           request.form.get("session_id") or \
           request.headers.get("X-Session-Id")
    if not sess:
        return jsonify(error="missing session_id"), 400
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Schedule and wait in running loop (ASGI/Werkzeug can be running)
            fut = asyncio.run_coroutine_threadsafe(
                __import__("app").services.streaming_asr.stream_manager.asr_end(sess, wait_for_final=True),
                loop
            )
            fut.result(timeout=10)
        else:
            loop.run_until_complete(__import__("app").services.streaming_asr.stream_manager.asr_end(sess, wait_for_final=True))
    except Exception:
        # As a fallback, try a direct asyncio.run (new loop)
        try:
            asyncio.run(__import__("app").services.streaming_asr.stream_manager.asr_end(sess, wait_for_final=True))
        except Exception:
            pass
    return jsonify(ok=True), 200
