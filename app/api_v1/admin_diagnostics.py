from __future__ import annotations
from flask import Blueprint, jsonify
import os, time

bp = Blueprint("admin_diag_v1", __name__, url_prefix="/api/v1/admin")

def _ok(name, details="ok"):  return {"name": name, "ok": True,  "details": details}
def _ng(name, details):       return {"name": name, "ok": False, "details": details}

@bp.get("/diagnostics")
def diagnostics_info():
    return jsonify(ok=True, version="v1", checks=[
        "health","db","ws_bus","tts","stt_stream","deepgram"
    ])

@bp.post("/diagnostics/run")
def diagnostics_run():
    results = []

    # health
    try:
        from .health import bp as _bp  # route present implies import ok
        results.append(_ok("health"))
    except Exception as e:
        results.append(_ng("health", f"missing: {e!r}"))

    # db (admin config readable)
    try:
        from app.services.admin_config import get_admin_config
        _ = get_admin_config()
        results.append(_ok("db"))
    except Exception as e:
        results.append(_ng("db", f"config read failed: {e!r}"))

    # ws bus
    try:
        from app.ws.bus import bus
        q = bus.subscribe("__diag__")
        bus.broadcast("__diag__", {"type": "diag", "text": "ok"})
        results.append(_ok("ws_bus"))
    except Exception as e:
        results.append(_ng("ws_bus", f"{e!r}"))

    # tts import available
    try:
        from app.services import tts_provider  # noqa
        results.append(_ok("tts"))
    except Exception as e:
        results.append(_ng("tts", f"{e!r}"))

    # streaming stt route present
    try:
        from app.api_v1.voice_stream import bp as _vp  # noqa
        results.append(_ok("stt_stream"))
    except Exception as e:
        results.append(_ng("stt_stream", f"{e!r}"))

    # Deepgram readiness (non-invasive)
    try:
        api_key_present = bool(os.environ.get("DEEPGRAM_API_KEY"))
        from app.services.streaming_asr.stream_manager import get_streaming_status
        st = get_streaming_status()
        ok = api_key_present and not st.get("breaker_open", False)
        details = f"api_key={'yes' if api_key_present else 'no'}, breaker_open={st.get('breaker_open')}, provider_errors={st.get('provider_errors')}"
        results.append({"name":"deepgram","ok": bool(ok), "details": details})
    except Exception as e:
        results.append(_ng("deepgram", f"{e!r}"))

    return jsonify(ok=True, results=results, ts=int(time.time()))
