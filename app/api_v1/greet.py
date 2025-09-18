
# app/api_v1/greet.py — Phase 0 hardening
from __future__ import annotations
import os, uuid
from flask import Blueprint, jsonify, request, session
from ..db import db
from ..ws.bus import bus
from ..services.suggestions import hygienic_suggestions
from ..middleware.csrf import ensure_csrf_headers
try:
    from ..api_v1.admin import _emit
except Exception:  # admin not required for greet
    def _emit(*a, **k):  # no-op
        pass

bp = Blueprint("greet", __name__)

def _session_id() -> str:
    # Prefer explicit session_id; fall back to cookie-bound session or 'default'
    sid = (request.args.get("session_id") or request.headers.get("X-Session-Id") or "").strip()
    try:
        db.memory.setdefault('sessions', {}).setdefault(sid, {'persona_id':'chip'})
    except Exception:
        pass
    if not sid:
        sid = session.get("sid") or "default"
    return sid

@bp.get("")
def greet():
    sid = _session_id()
    turns = db.memory.setdefault("greet_turns", {})
    # Allow clients to force a fresh greet for this session (e.g., on Start)
    try:
        _force = (request.args.get('reset') or request.args.get('force') or '').lower()
        if _force in ('1','true','yes'):
            try:
                turns.pop(sid, None)
                _emit('greet:reset', label='greet:reset', route='/api/v1/greet', session_id=sid)
            except Exception:
                pass
    except Exception:
        pass
    if sid in turns:
        existing = turns[sid]
        tid = (existing.get('tid') if isinstance(existing, dict) else existing)
        try:
            _emit('greet:req', label='greet:req (repeat)', route='/api/v1/greet', session_id=sid, turn_id=tid)
        except Exception:
            pass
        resp = jsonify({"ok": True, "turn_id": tid, "idempotent": True})
        return ensure_csrf_headers(resp), 200

    # Create a new turn id (UUID is acceptable per spec). Do NOT depend on vendors.
    tid = uuid.uuid4().hex
    turns[sid] = {'tid': tid, 'ts': __import__('time').time()}

    # Optionally schedule audio if vendors are configured. Otherwise, skip explicitly.
    audio_scheduled = False
    reason = None
    try:
        from ..api_v1.admin import _vendor_status_payload
        v = _vendor_status_payload()
        have_openai = bool(os.environ.get("OPENAI_API_KEY"))
        have_eleven = bool(v.get("elevenlabs")) or bool(os.environ.get("ELEVENLABS_API_KEY"))
    except Exception:
        have_openai = bool(os.environ.get("OPENAI_API_KEY"))
        have_eleven = bool(os.environ.get("ELEVENLABS_API_KEY"))
    if have_openai and have_eleven:
        try:
            from ..services.streaming import make_assistant_frames, schedule_frames
            # Build minimal greet text; persona preamble handled in streaming
            _tid, frames = make_assistant_frames("greet", sid)
            # Ensure make_assistant_frames returns our tid or ignore and use ours
            if isinstance(_tid, str):
                tid = _tid
                turns[sid] = {'tid': tid, 'ts': __import__('time').time()}
            schedule_frames(sid, frames, enable_nudge=False)
            audio_scheduled = True
            try:
                rec = turns.get(sid)
                if isinstance(rec, dict): rec['audio_scheduled'] = True
            except Exception:
                pass
            try:
                _emit('greet:scheduled', label='greet:scheduled', session_id=sid, n=len(frames))
            except Exception:
                pass
        except Exception as e:
            reason = f"vendor_error: {e.__class__.__name__}"
    else:
        reason = "missing_vendor_keys"

    payload = {"ok": True, "turn_id": tid}
    if not audio_scheduled:
        # Make the non-audio behavior explicit to avoid silent degrade
        payload["audio_scheduled"] = False
        if reason:
            payload["note"] = reason

    resp = jsonify(payload)
    # Enqueue initial 'state' and 'suggestions' frames regardless of audio path
    try:
        bus.broadcast(sid, {"type":"state","phase":"ready"})
        bus.broadcast(sid, {"type":"suggestions","turn_id": tid, "items": hygienic_suggestions("")})
    except Exception:
        pass
    try:
        _emit('greet:resp', label='greet:resp', session_id=sid, turn_id=tid, audio_scheduled=audio_scheduled)
    except Exception:
        pass
    return ensure_csrf_headers(resp), 200
