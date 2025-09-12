# app/api_v1/voice_stream.py
from __future__ import annotations
from flask import Blueprint, request, jsonify
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
