# voice.py
from __future__ import annotations
from flask import Blueprint, request, jsonify
import asyncio
import time

from ..services.streaming_asr.stream_manager import get_manager, asr_end

bp = Blueprint("voice", __name__, url_prefix="/api/v1/voice")

_RPS = {}
_RPS_WINDOW = 1.0
_RPS_MAX = 6
_MAX_CHUNK = 512 * 1024

def _rps_ok(sid: str) -> bool:
    now = time.time()
    arr = _RPS.get(sid) or []
    arr = [t for t in arr if now - t <= _RPS_WINDOW]
    if len(arr) >= _RPS_MAX:
        _RPS[sid] = arr
        return False
    arr.append(now)
    _RPS[sid] = arr
    return True

def _get_session_id() -> str | None:
    return (request.args.get("session_id")
        or (request.get_json(silent=True) or {}).get("session_id")
        or (request.get_json(silent=True) or {}).get("sid")
        or request.form.get("session_id")
        or request.headers.get("X-Session-Id")
    )

def _read_audio_bytes() -> bytes | None:
    f = request.files.get("chunk")
    if f:
        try: return f.read()
        except Exception: return None

    # JSON base64 (tests)
    if (request.content_type or "").startswith("application/json"):
        data = request.get_json(silent=True) or {}
        b64 = data.get("audio_b64") or data.get("audio_base64") or data.get("b64") or data.get("data")
        if isinstance(b64, str) and b64:
            import base64, binascii
            try: return base64.b64decode(b64, validate=True)
            except (binascii.Error, ValueError): return None

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
    if len(data) > _MAX_CHUNK:
        return jsonify(error="chunk too large", max_bytes=_MAX_CHUNK), 413

    # sequence (for tests / diagnostics)
    seq = None
    try:
        seq = int(request.headers.get("X-Seq", "0")) or None
    except Exception:
        seq = None
    if seq is None and (request.content_type or "").startswith("application/json"):
        payload = request.get_json(silent=True) or {}
        try: seq = int(payload.get("chunk_seq") or payload.get("seq") or 0) or None
        except Exception: seq = None

    mgr = get_manager()
    try:
        # assemble item dict (tests expect user_msg_id, chunk_seq, data)
        item = {"data": data}
        if (request.content_type or "").startswith("application/json"):
            payload = request.get_json(silent=True) or {}
            if "user_msg_id" in payload: item["user_msg_id"] = payload.get("user_msg_id")
            if "chunk_seq" in payload:
                try: item["chunk_seq"] = int(payload.get("chunk_seq"))
                except Exception: pass
        else:
            if request.headers.get("X-User-Msg-Id"): item["user_msg_id"] = request.headers.get("X-User-Msg-Id")
            try:
                if request.headers.get("X-Seq"): item["chunk_seq"] = int(request.headers.get("X-Seq"))
            except Exception: pass
        mgr.enqueue(sess, item)
    except Exception as e:
        return jsonify(error="enqueue_failed", detail=str(e)), 500
    return jsonify(ok=True, received_seq=(seq if seq is not None else 1)), 200

@bp.post("/end")
def voice_end():
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
        try:
            asyncio.run(asr_end(sess, wait_for_final=True))
        except Exception:
            pass
    return jsonify(ok=True), 200
