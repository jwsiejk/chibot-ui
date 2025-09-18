# app/api_v1/greet.py — Production-optimal greet:
# - Decouple LLM (text) from TTS (audio): always produce assistant_* frames if OPENAI_API_KEY is present.
# - TTS is optional and only influences the audio_scheduled flag.
# - Deterministic idempotency with optional reset & TTL.
# - Broadcasts 'state' + 'suggestions' regardless, so UI never looks “dead”.
# - Adds lightweight diagnostics via response headers and JSON payload.

from __future__ import annotations
import os, time, uuid
from flask import Blueprint, jsonify, request, session

from ..db import db
from ..ws.bus import bus
from ..services.suggestions import hygienic_suggestions
from ..middleware.csrf import ensure_csrf_headers

try:
    # Admin SSE emitter (optional)
    from ..api_v1.admin import _emit as _admin_emit  # type: ignore
except Exception:  # admin not required for greet
    def _admin_emit(*a, **k):  # no-op
        pass

bp = Blueprint("greet", __name__)

# --- Helpers -----------------------------------------------------------------

def _session_id() -> str:
    # Prefer explicit session_id (query/header), fall back to cookie-bound session or 'default'
    sid = (request.args.get("session_id") or request.headers.get("X-Session-Id") or "").strip()
    try:
        # Prime a minimal session record to scope persona, etc.
        db.memory.setdefault('sessions', {}).setdefault(sid, {'persona_id': 'chip'})
    except Exception:
        pass
    if not sid:
        sid = session.get("sid") or "default"
    try:
        session["sid"] = sid
    except Exception:
        pass
    return sid

def _now() -> float:
    return time.time()

# --- Route -------------------------------------------------------------------

@bp.get("")
def greet():
    sid = _session_id()
    turns = db.memory.setdefault("greet_turns", {})

    # Allow client to force a fresh greet for this session (e.g., on Start).
    # This is safe and explicit; also useful in multi-process hosting.
    _force = (request.args.get("reset") or request.args.get("force") or "").strip().lower()
    if _force in ("1", "true", "yes"):
        try:
            turns.pop(sid, None)
            _admin_emit("greet:reset", label="greet:reset", route="/api/v1/greet", session_id=sid)
        except Exception:
            pass

    # Idempotency with TTL: treat greets within the last 10 minutes as repeats unless reset/force was sent.
    TTL_SEC = 600
    if sid in turns:
        existing = turns[sid]
        last_ts = 0.0
        try:
            last_ts = float(existing.get("ts", 0.0)) if isinstance(existing, dict) else 0.0
        except Exception:
            last_ts = 0.0
        still_fresh = last_ts and (_now() - last_ts) < TTL_SEC
        if still_fresh and not _force:
            tid = existing.get("tid") if isinstance(existing, dict) else str(existing)
            try:
                _admin_emit('greet:req', label='greet:req (repeat)', route='/api/v1/greet', session_id=sid, turn_id=tid)
            except Exception:
                pass
            resp = jsonify({"ok": True, "turn_id": tid, "idempotent": True})
            # Attach tiny diagnostics for visibility via DevTools
            try:
                resp.headers['X-Worker-PID'] = str(os.getpid())
            except Exception:
                pass
            return ensure_csrf_headers(resp), 200

    # Create a new turn id (UUID is acceptable). Do NOT depend on vendors for this.
    tid = uuid.uuid4().hex
    turns[sid] = {"tid": tid, "ts": _now()}

    # Vendor presence checks (do not perform network calls here; just presence)
    have_openai = bool(os.environ.get("OPENAI_API_KEY"))
    have_eleven = bool(os.environ.get("ELEVENLABS_API_KEY"))

    # Prepare outputs
    audio_scheduled = False
    note = None

    # === PRODUCTION-OPTIMAL CHANGE: decouple LLM (text) from TTS (audio) ===
    # If OpenAI is present, *always* generate assistant_* frames and broadcast them.
    # Only *audio scheduling* depends on ElevenLabs.
    if have_openai:
        try:
            from ..services.streaming import make_assistant_frames, schedule_frames
            # Build assistant frames (this function will broadcast assistant_chunk + assistant_end, and optional suggestions)
            _tid, frames = make_assistant_frames("greet", sid, meta={"source": "greet"})
            # If make_assistant_frames decided a turn_id, prefer it (keeps correlation tidy)
            if isinstance(_tid, str) and _tid:
                tid = _tid
                turns[sid] = {"tid": tid, "ts": _now()}
            # Broadcast the frames (non-blocking; TTS is handled elsewhere or by downstream logic)
            schedule_frames(sid, frames, enable_nudge=False)
            # TTS availability influences only the audio flag (optional)
            if have_eleven:
                audio_scheduled = True
        except Exception as e:
            # If LLM path failed despite OPENAI presence, record a reason and continue to render suggestions/state
            note = f"llm_error:{e.__class__.__name__}"
    else:
        note = "missing_openai_key"

    # JSON payload back to caller (mainly for CSRF + client-side UX hooks)
    payload = {"ok": True, "turn_id": tid}
    if not audio_scheduled:
        payload["audio_scheduled"] = False
        if note:
            payload["note"] = note

    # Attach small diagnostics for visibility (readable in DevTools or Admin SSE)
    try:
        payload["diag"] = {
            "have_openai": bool(have_openai),
            "have_eleven": bool(have_eleven),
            "pid": os.getpid(),
        }
    except Exception:
        pass

    resp = jsonify(payload)
    # Mirror diagnostics to headers (handy in Network panel)
    try:
        resp.headers['X-Worker-PID'] = str(os.getpid())
        resp.headers['X-OpenAI'] = '1' if have_openai else '0'
        resp.headers['X-Eleven'] = '1' if have_eleven else '0'
    except Exception:
        pass

    # Enqueue initial 'state' + 'suggestions' frames regardless of audio/LLM success
    try:
        bus.broadcast(sid, {"type": "state", "phase": "ready"})
        bus.broadcast(sid, {"type": "suggestions", "turn_id": tid, "items": hygienic_suggestions("")})
    except Exception:
        pass

    try:
        _admin_emit('greet:resp', label='greet:resp', session_id=sid, turn_id=tid, audio_scheduled=audio_scheduled)
    except Exception:
        pass

    return ensure_csrf_headers(resp), 200
