
import base64
from flask import Blueprint, jsonify, request, session
from ..db import db
from ..security_state import get_user
from ..services.mailer import send_transcript
from ..services.streaming import (
    make_assistant_frames,
    schedule_frames,
    merge_suggestions,
    build_suggestion_items,
)
from ..services.greet_idempotency import clear_greet_turn_cache
from ..nlu.classifier import classify as classify_turn
from ..dialog.policy import pick as pick_dialog_policy
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
    # Rate-limit guard: skip for control commands
    data = request.get_json(silent=True) or {}
    cmd = (data.get('cmd') or '').strip().lower()
    if cmd in ('nudge','interrupt','end_session'):
        return None
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

    # Commands that do not require idempotency
    if cmd == "interrupt":
        tid = (data.get("turn_id") or "").strip()
        try:
            bus.cancel_turn(sid, tid)
        except Exception:
            pass
        try:
            bus.broadcast(sid, {"type":"state","phase":"ready"})
        except Exception:
            pass
        return jsonify(ok=True, interrupted=True), 200

    if cmd == "nudge":
        from ..session_state import can_nudge, mark_nudge
        cfg = db.get_config()
        backoff_after = int(cfg.get("nudge_backoff_after_ignored", 2))
        if can_nudge(sid, backoff_after):
            mark_nudge(sid)
            try:
                from ..services.suggestions import hygienic_suggestions
                merged = merge_suggestions(hygienic_suggestions(""))
                if merged:
                    bus.broadcast(sid, {"type":"suggestions","turn_id":"nudge","items": build_suggestion_items(merged)})
            except Exception:
                pass
            return jsonify(ok=True, nudged=True), 200
        return jsonify(ok=True, nudged=False), 200

    if cmd == "end_session":
        from time import time as _now
        to_email = (session.get("user") or {}).get("email")
        emailed = False
        if to_email:
            try:
                emailed = bool(send_transcript(db=db, session_id=sid, ended_at=_now(), to_email=to_email))
            except Exception:
                emailed = False
        clear_greet_turn_cache(sid)
        return jsonify(ok=True, emailed=emailed), 200

    # Normal text turn path
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

    # Try to schedule frames; if provider unavailable, still return ok with tid (offline-safe)
    try:
        request_meta = dict(data) if isinstance(data, dict) else {}
        nested_meta = request_meta.pop("meta", None)
        if isinstance(nested_meta, dict):
            request_meta.update(nested_meta)
        request_meta.setdefault("source", "user_http")
        nlu_result = classify_turn(text, meta=request_meta)
        policy = pick_dialog_policy(nlu_result) or {}
        enriched_meta = {
            **request_meta,
            "nlu": nlu_result,
            "action": policy.get("action"),
            "verbosity": policy.get("verbosity"),
            "show_suggestions": policy.get("show_suggestions"),
        }
        _provider_tid, frames = make_assistant_frames(
            text,
            sid,
            meta=enriched_meta,
            correlation_user_msg_id=user_msg_id,
            broadcast_immediately=False,
        )
        # ✅ Broadcast them to the WS client
        schedule_frames(sid, frames, correlation_user_msg_id=user_msg_id)
        try:
            from ..api_v1.admin import _emit
            _emit('chat:scheduled', label='chat:scheduled', session_id=sid, n=len(frames))
            _emit('chat:ok', label='chat:ok – frames ready', turn_id=tid, n=len(frames))
        except Exception:
            pass
        return jsonify(ok=True, user_msg_id=user_msg_id, turn_id=tid), 200
    except Exception as e:
        try:
            from ..api_v1.admin import _emit
            _emit('chat:ok', label='chat:ok – no vendor (offline fallback)', turn_id=tid, n=0)
        except Exception:
            pass
        return jsonify(ok=True, user_msg_id=user_msg_id, turn_id=tid), 200

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

@bp.post("/")
def chat_entry():
    data = request.get_json(silent=True) or {}
    sid = (data.get("session_id") or "").strip() or (request.args.get("session_id") or "").strip()
    cmd = (data.get("cmd") or "").strip().lower()
    text = (data.get("text") or "").strip()
    user_msg_id = request.headers.get("Idempotency-Key") or data.get("user_msg_id")
    if not sid:
        return jsonify({"ok": False, "error": "missing_session_id"}), 400

    # Commands
    if cmd == "interrupt":
        tid = (data.get("turn_id") or "").strip()
        try:
            bus.cancel_turn(sid, tid)
        except Exception:
            pass
        # signal ready state
        try:
            bus.broadcast(sid, {"type":"state","phase":"ready"})
        except Exception:
            pass
        return jsonify({"ok": True, "interrupted": True})

    if cmd == "nudge":
        from ..session_state import can_nudge, mark_nudge
        cfg = db.get_config()
        backoff_after = int(cfg.get("nudge_backoff_after_ignored", 2))
        if can_nudge(sid, backoff_after):
            mark_nudge(sid)
            try:
                from ..services.suggestions import hygienic_suggestions
                merged = merge_suggestions(hygienic_suggestions(""))
                if merged:
                    bus.broadcast(sid, {"type":"suggestions","turn_id":"nudge","items": build_suggestion_items(merged)})
            except Exception:
                pass
            return jsonify({"ok": True, "nudged": True})
        return jsonify({"ok": True, "nudged": False})

    if cmd == "end_session":
        # Send transcript to the logged-in user
        to_email = session.get("email")
        emailed = False
        if to_email:
            try:
                from time import time as _now
                emailed = bool(send_transcript(db=db, session_id=sid, ended_at=_now(), to_email=to_email))
            except Exception:
                emailed = False
        return jsonify({"ok": True, "emailed": emailed})

    # Otherwise, treat as a normal text turn
    if not text:
        return jsonify({"ok": False, "error": "empty_text"}), 400
    request_meta = dict(data) if isinstance(data, dict) else {}
    nested_meta = request_meta.pop("meta", None)
    if isinstance(nested_meta, dict):
        request_meta.update(nested_meta)
    request_meta.setdefault("source", "user_http")
    nlu_result = classify_turn(text, meta=request_meta)
    policy = pick_dialog_policy(nlu_result) or {}
    enriched_meta = {
        **request_meta,
        "nlu": nlu_result,
        "action": policy.get("action"),
        "verbosity": policy.get("verbosity"),
        "show_suggestions": policy.get("show_suggestions"),
    }
    _provider_tid, frames = make_assistant_frames(
        text,
        sid,
        meta=enriched_meta,
        correlation_user_msg_id=user_msg_id,
        broadcast_immediately=False,
    )
    # ✅ Broadcast them to the WS client (fallback path too)
    schedule_frames(sid, frames, correlation_user_msg_id=user_msg_id)
    turn_id = None
    for fr in frames:
        if fr.get("type") in ("assistant_chunk","text") and fr.get("turn_id"):
            turn_id = fr.get("turn_id")
            break
    return jsonify({"ok": True, "turn_id": turn_id})
