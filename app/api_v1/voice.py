# voice.py
from __future__ import annotations

import asyncio
import time
from typing import Optional

from flask import Blueprint, jsonify, request

from ..services.streaming_asr.stream_manager import (
    get_manager,
    asr_end,
)

bp = Blueprint("voice", __name__, url_prefix="/api/v1/voice")

# --- simple per-session rate limit (server-side guard for /chunk) ------------
_RPS = {}                 # session_id -> [timestamps]
_RPS_WINDOW = 1.0         # seconds
_RPS_MAX = 30             # allow ~10–11 rps @ 96ms cadence with headroom
_MAX_CHUNK = 512 * 1024   # 512 KiB single-chunk hard cap


def _now() -> float:
    return time.time()


def _get_session_id() -> Optional[str]:
    # Prefer query param (what the client uses), then form/json fallbacks
    sid = request.args.get("session_id")
    if not sid:
        try:
            if request.form:
                sid = request.form.get("session_id")
        except Exception:
            pass
    if not sid:
        try:
            payload = request.get_json(silent=True) or {}
            sid = payload.get("session_id")
        except Exception:
            sid = None
    return sid or None


def _rps_ok(session_id: str) -> bool:
    now = _now()
    q = _RPS.get(session_id) or []
    q = [t for t in q if (now - t) <= _RPS_WINDOW]
    ok = len(q) < _RPS_MAX
    if ok:
        q.append(now)
    _RPS[session_id] = q
    return ok


def _read_audio_bytes() -> bytes:
    try:
        ctype = (request.content_type or "").lower()
        if ctype.startswith("multipart/form-data"):
            # Expect a single file field named "chunk"
            f = request.files.get("chunk")
            return f.read() if f else b""
        if ctype.startswith("application/octet-stream"):
            return request.get_data(cache=False) or b""
        # Fallback — some clients may POST the raw body with no explicit type.
        data = request.get_data(cache=False) or b""
        return data
    except Exception:
        return b""


@bp.post("/chunk")
def voice_chunk():
    """
    Production path:
      - Receive one MediaRecorder slice (Opus/WebM) via multipart POST.
      - Guard size & RPS.
      - Forward to the streaming ASR manager (Deepgram bridge).
    Returns {ok:true, received_seq:n}
    """
    sess = _get_session_id()
    if not sess:
        return jsonify(error="missing_session_id"), 400

    if not _rps_ok(sess):
        return jsonify(error="rate_limited"), 429

    data = _read_audio_bytes()
    if not data:
        return jsonify(error="missing_or_invalid_chunk"), 400
    if len(data) > _MAX_CHUNK:
        return jsonify(error="chunk_too_large", max_bytes=_MAX_CHUNK), 413

    # Optional sequence + message id (for diagnostics/ordering)
    seq: Optional[int] = None
    try:
        if request.headers.get("X-Seq") is not None:
            seq = int(request.headers.get("X-Seq") or "0") or None
    except Exception:
        seq = None

    item = {"data": data}
    try:
        if request.headers.get("X-User-Msg-Id"):
            item["user_msg_id"] = request.headers.get("X-User-Msg-Id")
        if seq is not None:
            item["chunk_seq"] = seq
    except Exception:
        pass

    mgr = get_manager()
    try:
        mgr.enqueue(sess, item)
    except Exception as e:
        return jsonify(error="enqueue_failed", detail=str(e)), 500

    # Keep a simple echo for tests/diag
    return jsonify(ok=True, received_seq=(seq if seq is not None else 1)), 200


@bp.post("/end")
def voice_end():
    """
    Signal end-of-turn for the session. We wait (bounded) for a final ASR
    result to arrive before closing.
    """
    sess = _get_session_id()
    if not sess:
        return jsonify(error="missing_session_id"), 400

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We might be on a worker thread; use thread-safe submission.
            fut = asyncio.run_coroutine_threadsafe(
                asr_end(sess, wait_for_final=True), loop
            )
            fut.result(timeout=12)
        else:
            loop.run_until_complete(asr_end(sess, wait_for_final=True))
    except Exception:
        try:
            # Last resort — spin our own loop just for the close.
            asyncio.run(asr_end(sess, wait_for_final=True))
        except Exception:
            pass

    return jsonify(ok=True), 200
