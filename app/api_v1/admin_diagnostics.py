# app/api_v1/admin_diagnostics.py
from __future__ import annotations
import os, time
from flask import Blueprint, jsonify

# Live streaming counters (partials/finals/provider_errors/breaker_open)
from ..services.streaming_asr.stream_manager import get_streaming_status

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

def _rate_limits_snapshot() -> dict:
    # Defaults should mirror app/middleware/rate_limit.py for correctness
    window_s = _num_env("RATE_LIMIT_WINDOW_S", 1.0)
    max_chat = _num_env("RATE_LIMIT_MAX", 3)
    max_voice = _num_env("RATE_LIMIT_MAX_VOICE_CHUNK", 16)
    return {
        "chat": {"window_s": window_s, "max_per_window": max_chat},
        "voice_chunk": {"window_s": window_s, "max_per_window": max_voice},
    }

def _vendor_status_snapshot() -> dict:
    return {
        "deepgram_enabled": _bool_env("DEEPGRAM_API_KEY"),
        "elevenlabs_enabled": _bool_env("ELEVENLABS_API_KEY"),
        "openai_enabled": _bool_env("OPENAI_API_KEY"),
        # Optional extras if configured:
        "deepgram_listen_url": os.environ.get("DEEPGRAM_LISTEN_URL") or "wss://api.deepgram.com/v1/listen",
        "elevenlabs_voice_id": os.environ.get("ELEVENLABS_VOICE_ID") or "",
    }

# ------------------------- routes -------------------------

@bp.get("")
def diag_root():
    """Compact diagnostics bundle used by the Admin page."""
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
    """Lightweight endpoint the UI can poll during Mic Mode."""
    return jsonify({ "ok": True, **get_streaming_status() }), 200

@bp.get("/vendor_status")
def vendor_status():
    """Strict vendor flags for Diagnostics (no guessing)."""
    v = _vendor_status_snapshot()
    return jsonify({"ok": True, **v}), 200

@bp.get("/rate_limits")
def rate_limits():
    """Strict rate-limit settings as seen by the app process."""
    rl = _rate_limits_snapshot()
    return jsonify({"ok": True, **rl}), 200

@bp.post("/run")
def run_smoke():
    """Simple placeholder (kept for compatibility with older Admin buttons)."""
    # If you later want to kick internal checks, you can extend this safely.
    return jsonify({"ok": True, "ts": int(time.time() * 1000)}), 200
