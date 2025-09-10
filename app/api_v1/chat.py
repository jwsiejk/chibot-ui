import base64
from flask import Blueprint, jsonify, request, session
from ..db import db
from ..security_state import get_user
from ..services.mailer import send_transcript
from ..services.streaming import make_assistant_frames, schedule_frames
from ..middleware.rate_limit import limit, check_now
from ..ws.bus import bus

bp = Blueprint("chat", __name__)
_TTS_MEMO = {}

@bp.before_request
def _chat_rl_guard():
    # Rate-limit guard (returns a response on limit breach)
    rv = check_now('chat')
    return rv

@limit("chat")
@bp.post("/")
@bp.post("")
def post_chat():
    # Unified chat entrypoint used by the UI
    data = request.get_json(silent=True) or {}
    cmd = (data.get("cmd") or "").strip().lower()
    text = (data.get("text") or "").strip()

    # Establish session/user
    email = (session.get("user") or {}).get("email") or (get_user() or "")
    sid = data.get("sid") or session.get("sid") or "default"

    # Nudge controls
    if cmd == "nudge":
        s = db.ensure_session(sid, email)
        if s.get("nudges", 0) < 1:
            try:
                from ..policy.nudges import arm_nudge
                arm_nudge(sid)
                s["nudges"] = s.get("nudges", 0) + 1
                for fr in [{"type":"state","phase":"assistant_speaking"},
                           {"type":"assistant_chunk","text":"(nudge) Just checking in — want me to continue?"},
                           {"type":"assistant_end"}]:
                    try:
                        bus.broadcast(sid, fr)
                    except Exception:
                        pass
                return jsonify({"ok": True, "nudged": True, "count": s["nudges"]})
            except Exception:
                pass
        return jsonify({"ok": True, "nudged": False, "count": s.get("nudges", 0)})
    if cmd == "end_session":
        body = db.get_transcript(sid)
        try:
            send_transcript(email, "Ask Chip — transcript", body)
        except Exception:
            pass
        return jsonify({"ok": True, "emailed": True})

    # Normal chat turn
    if text:
        db.ensure_session(sid, email)
        db.add_message(sid, "user", text)
        try:
            _emit('chat:req', label='chat:req – user_text', route='/api/v1/chat', text=text)
        except Exception:
            pass
        try:
            from ..policy.nudges import cancel_nudge
            cancel_nudge(sid)
        except Exception:
            pass

    # Compose + schedule frames (TTS is internally gated by feature_audio)
    try:
        tid, frames = make_assistant_frames((text or "chat"), sid)
    except Exception:
        from ..services.streaming import make_assistant_frames_text_only
        tid, frames = make_assistant_frames_text_only((text or "chat"), sid)
    schedule_frames(sid, frames)
    try:
        _emit('chat:scheduled', label='chat:scheduled', session_id=sid, n=len(frames))
    except Exception:
        pass
    try:
        _emit('chat:ok', label='chat:ok – frames ready', turn_id=tid, n=len(frames))
    except Exception:
        pass
    return jsonify({"ok": True, "turn_id": tid})

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
