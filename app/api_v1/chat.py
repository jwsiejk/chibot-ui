
import base64
from flask import Blueprint, jsonify, request, session
from ..db import db
from ..security_state import get_user
from ..services.mailer import send_transcript
from ..services.streaming import make_assistant_frames, schedule_frames
from ..middleware.rate_limit import limit, check_now
from ..ws.bus import bus
import os, uuid

bp = Blueprint("chat", __name__)
_TTS_MEMO = {}

# Phase 1/2: map Idempotency-Key header to user_msg_id for correlation
def _get_user_msg_id():
    return (request.headers.get('Idempotency-Key') or request.headers.get('X-User-Msg-Id') or '').strip()

@bp.before_request
def _chat_rl_guard():
    # Rate-limit guard (returns a response on limit breach)
    rv = check_now('chat')
    return rv

@limit("chat")
@bp.post("")
def post_chat():
    # Unified chat entrypoint used by the UI
    data = request.get_json(silent=True) or {}
    cmd = (data.get("cmd") or "").strip().lower()
    text = (data.get("text") or "").strip()

    # Establish session/user
    email = (session.get("user") or {}).get("email") or (get_user() or "")
    sid = (data.get("session_id") or data.get("sid") or request.args.get("session_id") or session.get("sid") or "default")
    try:
        session["sid"] = sid
    except Exception:
        pass

    user_msg_id = _get_user_msg_id()
    if not user_msg_id:
        return jsonify(ok=False, error="missing_idempotency_key", detail="Provide Idempotency-Key header", session_id=sid), 400

    # Typed chat idempotency store
    idem = db.memory.setdefault("chat_turns", {}).setdefault(sid, {})

    # If duplicate, return same turn_id and mark idempotent
    if user_msg_id in idem:
        tid = idem[user_msg_id]
        try:
            from ..api_v1.admin import _emit
            _emit('chat:idempotent', session_id=sid, user_msg_id=user_msg_id, turn_id=tid)
        except Exception:
            pass
        return jsonify(ok=True, user_msg_id=user_msg_id, turn_id=tid, idempotent=True), 200

    # First time this user_msg_id seen for this session: allocate a turn_id
    tid = uuid.uuid4().hex
    idem[user_msg_id] = tid

    # If vendors are available, schedule frames; else explicit error (no silent degrade)
    have_openai = bool(os.environ.get("OPENAI_API_KEY"))
    have_eleven = bool(os.environ.get("ELEVENLABS_API_KEY"))
    if have_openai and have_eleven:
        try:
            tid2, frames = make_assistant_frames(text, sid)
            # Prefer provider-generated turn_id if returned
            if isinstance(tid2, str):
                tid = tid2
                idem[user_msg_id] = tid
            schedule_frames(sid, frames, correlation_user_msg_id=user_msg_id)
            try:
                from ..api_v1.admin import _emit
                _emit('chat:scheduled', label='chat:scheduled', session_id=sid, n=len(frames))
                _emit('chat:ok', label='chat:ok – frames ready', turn_id=tid, n=len(frames))
            except Exception:
                pass
            return jsonify(ok=True, user_msg_id=user_msg_id, turn_id=tid), 200
        except Exception as e:
            # Fall through to explicit error below
            reason = f"vendor_error:{e.__class__.__name__}"
            return jsonify(ok=False, error=reason, user_msg_id=user_msg_id, turn_id=tid, session_id=sid), 500
    else:
        # Explicit, actionable error (no mocks, no silent degrade)
        return jsonify(ok=False, error="missing_vendor_keys", user_msg_id=user_msg_id, turn_id=tid, session_id=sid,
                       detail="Set OPENAI_API_KEY and ELEVENLABS_API_KEY to enable chat synthesis"), 400

@limit("voice_tts")
@bp.post("/tts-with-visemes")
def tts_with_visemes():
    from ..services.tts_provider import get_tts_provider
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    cfg = db.get_config()
    if text in _TTS_MEMO:
        a, v = _TTS_MEMO[text]
    else:
        a_bytes, v = get_tts_provider(cfg).synth(text)
        a = base64.b64encode(a_bytes).decode("ascii")
        _TTS_MEMO[text] = (a, v)
    try:
        from ..api_v1.admin import _emit
        _emit('tts', chars=len(text))
    except Exception:
        pass
    return jsonify({"ok": True, "audio_b64": a, "visemes": v})
