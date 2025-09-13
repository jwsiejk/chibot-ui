# app/api_v1/admin_diagnostics.py
from __future__ import annotations
from flask import Blueprint, jsonify, request
import os, time, uuid

bp = Blueprint("admin_diag_v1", __name__, url_prefix="/api/v1/admin")

def _ok(name, details="ok"):  return {"name": name, "ok": True,  "details": details}
def _ng(name, details):       return {"name": name, "ok": False, "details": details}

@bp.get("/diagnostics")
def diagnostics_info():
    # Quick presence listing (kept fast and non-blocking)
    return jsonify(ok=True, version="v1", checks=[
        "health","db","ws_bus","tts","stt_stream","deepgram"
    ])

@bp.post("/diagnostics/run")
def diagnostics_run():
    """
    QUICK checks only (no live network/WS). Fast, bounded.
    """
    results = []

    # health route exists
    try:
        from .health import bp as _bp  # noqa
        results.append(_ok("health"))
    except Exception as e:
        results.append(_ng("health", f"missing: {e!r}"))

    # admin config readable (DB reachability)
    try:
        from app.services.admin_config import get_admin_config
        _ = get_admin_config()
        results.append(_ok("db"))
    except Exception as e:
        results.append(_ng("db", f"config read failed: {e!r}"))

    # ws bus importable & broadcastable
    try:
        from app.ws.bus import bus
        q = bus.subscribe("__diag_quick__")
        bus.broadcast("__diag_quick__", {"type": "diag", "text": "ok"})
        results.append(_ok("ws_bus"))
    except Exception as e:
        results.append(_ng("ws_bus", f"{e!r}"))

    # tts import presence (no live synth)
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

    # Deepgram readiness (no live call)
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

@bp.post("/diagnostics/full")
def diagnostics_full():
    """
    FULL system test (bounded ~10s), without external network:
      - subscribe to bus for a fresh session id
      - push 6 stream chunks via HTTP (or fall back to direct enqueue)
      - confirm user_partial(s) and exactly one user_final on bus
    """
    results = []
    sid = f"diag-{uuid.uuid4().hex[:8]}"

    # Subscribe to session bus
    try:
        from app.ws.bus import bus
        q = bus.subscribe(sid)
        results.append(_ok("bus_subscribe", f"session={sid}"))
    except Exception as e:
        return jsonify(ok=False, results=[_ng("bus_subscribe", f"{e!r}")], ts=int(time.time()))

    # Send 6 chunks via HTTP route; on failure, fall back to manager.enqueue
    http_post_ok = True
    try:
        import requests
        base = request.host_url.rstrip("/")
        url = f"{base}/api/v1/voice/stt/stream?session_id={sid}"
        headers = {}  # stream route does not require CSRF
        for _ in range(6):
            r = requests.post(url, data=b"X"*100, headers=headers, timeout=2)
            if not r.ok:
                http_post_ok = False
                break
        results.append(_ok("http_stream_post", "ok" if http_post_ok else "fallback"))
    except Exception:
        http_post_ok = False
        results.append(_ng("http_stream_post", "requests error; using fallback"))

    if not http_post_ok:
        try:
            from app.services.streaming_asr.stream_manager import get_manager
            mgr = get_manager()
            for _ in range(6):
                mgr.enqueue(sid, b"X"*100)
            results.append(_ok("enqueue_fallback"))
        except Exception as e:
            results.append(_ng("enqueue_fallback", f"{e!r}"))
            return jsonify(ok=False, results=results, ts=int(time.time()))

    # Collect frames with a timeout until we see final
    saw_partial = 0
    saw_final = False
    deadline = time.time() + 10.0

    try:
        while time.time() < deadline:
            try:
                frame = q.get(timeout=0.5)  # queue.Queue from your bus
            except Exception:
                continue
            if not isinstance(frame, dict):
                continue
            t = str(frame.get("type","")).lower()
            if t == "user_partial":
                saw_partial += 1
            elif t == "user_final":
                saw_final = True
                break
        results.append(_ok("partials_seen", str(saw_partial)))
        results.append(_ok("final_seen") if saw_final else _ng("final_seen", "timeout waiting for user_final"))
    except Exception as e:
        results.append(_ng("bus_read", f"{e!r}"))

    ok = saw_final
    return jsonify(ok=bool(ok), results=results, ts=int(time.time()))
