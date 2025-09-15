from __future__ import annotations

import os
import time
from flask import Blueprint, jsonify

# Streaming ASR counters (partials, finals, provider_errors, breaker_open)
from app.services.streaming_asr.stream_manager import get_streaming_status

bp = Blueprint("admin_diag_v1", __name__, url_prefix="/api/v1/admin/diagnostics")


# ----------------------- helpers -----------------------

def _bool_env(name: str) -> bool:
    val = os.environ.get(name)
    return bool(val and str(val).strip())

def _num_env(name: str, default: float | int):
    raw = os.environ.get(name, "")
    if raw is None or str(raw).strip() == "":
        return default
    try:
        if isinstance(default, int):
            return int(float(raw))
        return float(raw)
    except Exception:
        return default

def _vendor_status_snapshot() -> dict:
    return {
        "deepgram_enabled": _bool_env("DEEPGRAM_API_KEY"),
        "elevenlabs_enabled": _bool_env("ELEVENLABS_API_KEY"),
        "openai_enabled": _bool_env("OPENAI_API_KEY"),
        "deepgram_listen_url": os.environ.get("DEEPGRAM_LISTEN_URL") or "wss://api.deepgram.com/v1/listen",
        "elevenlabs_voice_id": os.environ.get("ELEVENLABS_VOICE_ID") or "",
    }

def _rate_limits_snapshot() -> dict:
    window_s = _num_env("RATE_LIMIT_WINDOW_S", 1.0)
    max_chat = _num_env("RATE_LIMIT_MAX", 3)
    max_voice = _num_env("RATE_LIMIT_MAX_VOICE_CHUNK", 16)
    return {
        "chat": {"window_s": window_s, "max_per_window": max_chat},
        "voice_chunk": {"window_s": window_s, "max_per_window": max_voice},
    }


# ------------------------- routes -------------------------

@bp.get("")
def diag_root():
    """
    High-level snapshot used by the Admin Diagnostics page.
    """
    out = {
        "ok": True,
        "vendors": _vendor_status_snapshot(),
        "rate_limits": _rate_limits_snapshot(),
        "streaming": get_streaming_status(),
        "ts": int(time.time() * 1000),
    }
    return jsonify(out), 200


@bp.get("/streaming_status")
def streaming_status():
    """
    Lightweight endpoint the UI can poll during Mic Mode to show:
    - partials / finals
    - provider_errors
    - breaker_open
    """
    return jsonify({"ok": True, **get_streaming_status()}), 200


@bp.get("/vendor_status")
def vendor_status():
    return jsonify({"ok": True, **_vendor_status_snapshot()}), 200


@bp.get("/rate_limits")
def rate_limits():
    return jsonify({"ok": True, **_rate_limits_snapshot()}), 200


@bp.post("/run")
def run_smoke():
    # Placeholder "run" hook so the UI has a consistent action endpoint.
    return jsonify({"ok": True, "ts": int(time.time() * 1000)}), 200
