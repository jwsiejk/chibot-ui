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



