# voice_stream.py
from __future__ import annotations
from flask import Blueprint, request, jsonify
import asyncio
import time

from ..services.streaming_asr.stream_manager import get_manager, asr_end

bp = Blueprint("voice_stream_v1", __name__, url_prefix="/api/v1/voice")

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
    try:
        body = request.get_data(cache=False) or b""
        return body if body else None
    except Exception:
        return None


