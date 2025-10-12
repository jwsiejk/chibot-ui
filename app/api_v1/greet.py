# app/api_v1/greet.py — Production-optimal greet (Neon-backed idempotency)
from __future__ import annotations
import os, time
from flask import Blueprint, jsonify, request, session

from ..db import db
from ..ws.bus import bus
from ..session_state import set_phase, should_emit_phase
from ..services.suggestions import hygienic_suggestions
from ..services.streaming import merge_suggestions, build_suggestion_items, prepare_turn_metadata
from ..middleware.csrf import ensure_csrf_headers
from ..services.greet_idempotency import get_or_create_greet_turn, DEFAULT_TTL_SEC

try:
    from ..api_v1.admin import _emit as _admin_emit  # type: ignore
except Exception:
    def _admin_emit(*a, **k):  # no-op
        pass

bp = Blueprint("greet", __name__)

def _session_id() -> str:
    sid = (request.args.get("session_id") or request.headers.get("X-Session-Id") or "").strip()
    try:
        db.memory.setdefault('sessions', {}).setdefault(sid or "default", {'persona_id': 'chip'})
    except Exception:
        pass
    if not sid:
        sid = session.get("sid") or "default"
    try:
        session["sid"] = sid
    except Exception:
        pass
    return sid

@bp.get("")
def greet():
    sid = _session_id()

    # Allow client to force a fresh greet for this session (e.g., on Start)
    force_flag = (request.args.get("reset") or request.args.get("force") or "").strip().lower() in ("1", "true", "yes")

    # Shared, Neon-backed idempotency (works across workers)
    tid, idempotent = get_or_create_greet_turn(sid, force=force_flag, ttl_sec=DEFAULT_TTL_SEC)

    have_openai = bool(os.environ.get("OPENAI_API_KEY"))
    have_eleven = bool(os.environ.get("ELEVENLABS_API_KEY"))

    # Optional: auto-arm mic after TTS greet begins.
    # Set ASKCHIP_AUTO_ARM_MS to a number (e.g., "250") to enable the hint and a control frame.
    auto_arm_env = (os.environ.get("ASKCHIP_AUTO_ARM_MS") or "").strip()
    auto_arm_ms: int | None = None
    if auto_arm_env:
        try:
            auto_arm_ms = max(0, int(auto_arm_env))
        except Exception:
            auto_arm_ms = 250

    audio_scheduled = False
    note = None
    scheduled_frames = []

    if have_openai:
        try:
            from ..services.streaming import make_assistant_frames, schedule_frames
            greet_meta, _, _ = prepare_turn_metadata("greet", {"source": "greet", "channel": "http"})
            _tid, frames = make_assistant_frames(
                "greet",
                sid,
                meta=greet_meta,
                broadcast_immediately=False,
            )
            # Prefer the LLM’s chosen turn if it returns one (keep correlation tidy)
            if isinstance(_tid, str) and _tid:
                tid = _tid
            schedule_frames(sid, frames)
            scheduled_frames = frames
            if have_eleven:
                audio_scheduled = True
        except Exception as e:
            note = f"llm_error:{e.__class__.__name__}"
            import traceback as _tb
            try:
                trace = _tb.format_exc()
            except Exception:
                trace = ""
            try:
                fallback_text = "Hi, I’m Chip. How can I help today?"
                from ..services.streaming import make_assistant_frames, schedule_frames
                fallback_meta, _, _ = prepare_turn_metadata(fallback_text, {"source":"greet_fallback", "channel": "http"})
                _tid2, frames2 = make_assistant_frames(
                    fallback_text,
                    sid,
                    meta=fallback_meta,
                    broadcast_immediately=False,
                )
                if isinstance(_tid2, str) and _tid2:
                    tid = _tid2
                schedule_frames(sid, frames2)
                scheduled_frames = frames2
                if have_eleven:
                    audio_scheduled = True
            except Exception:
                pass
    else:
        note = "missing_openai_key"

    # ---- Response payload ----------------------------------------------------
    payload: dict = {"ok": True, "turn_id": tid, "idempotent": bool(idempotent)}
    payload["audio_scheduled"] = bool(audio_scheduled)
    if note:
        payload["note"] = note

    # If audio is scheduled and auto-arm is enabled, include a client hint.
    if audio_scheduled and auto_arm_ms is not None:
        payload["arm_mic_after_ms"] = int(auto_arm_ms)
        payload["autostart_hint"] = "arm_mic"

    try:
        payload["diag"] = {
            "have_openai": bool(have_openai),
            "have_eleven": bool(have_eleven),
            "pid": os.getpid(),
        }
        try:
            if 'trace' in locals() and note:
                payload["diag"]["trace"] = trace
        except Exception:
            pass
    except Exception:
        pass

    resp = jsonify(payload)
    try:
        resp.headers['X-Worker-PID'] = str(os.getpid())
        resp.headers['X-OpenAI'] = '1' if have_openai else '0'
        resp.headers['X-Eleven'] = '1' if have_eleven else '0'
        resp.headers['X-Idempotent'] = '1' if idempotent else '0'
        resp.headers['X-Greet-TTL'] = str(DEFAULT_TTL_SEC)
    except Exception:
        pass

    # ---- Wake the UI: state + suggestions (avoid double-sending) ------------
    state_already_scheduled = any(
        isinstance(fr, dict) and fr.get("type") == "state" for fr in scheduled_frames
    )
    suggestions_already_scheduled = any(
        isinstance(fr, dict) and fr.get("type") == "suggestions" for fr in scheduled_frames
    )

    try:
        if not state_already_scheduled:
            if should_emit_phase(sid, "ready"):
                set_phase(sid, "ready", emitted=True)
                bus.broadcast(sid, {"type": "state", "phase": "ready"})
            else:
                set_phase(sid, "ready")
        if not suggestions_already_scheduled:
            merged = merge_suggestions(hygienic_suggestions(""))
            if merged:
                bus.broadcast(sid, {"type": "suggestions", "turn_id": tid, "items": build_suggestion_items(merged)})
    except Exception:
        pass

    # ---- Optional: control frame to auto-arm mic on the client --------------
    try:
        if audio_scheduled and auto_arm_ms is not None:
            bus.broadcast(sid, {"type": "control", "op": "arm_mic", "delay_ms": int(auto_arm_ms)})
            _admin_emit('mic:auto_arm_suggested', session_id=sid, turn_id=tid, delay_ms=int(auto_arm_ms))
    except Exception:
        pass

    # ---- Admin breadcrumbs ---------------------------------------------------
    try:
        _admin_emit(
            'greet:resp',
            label=('greet:resp (repeat)' if idempotent else 'greet:resp'),
            session_id=sid,
            turn_id=tid,
            tts_status=db.memory.get('tts_status', {}).get(sid, {}).get(str(tid) if tid else 'greet', {}),
            idempotent=bool(idempotent),
            audio_scheduled=bool(audio_scheduled),
            arm_mic_after_ms=(int(auto_arm_ms) if (audio_scheduled and auto_arm_ms is not None) else None),
        )
    except Exception:
        pass

    return ensure_csrf_headers(resp), 200
