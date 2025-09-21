# app/services/streaming.py — Production-grade assistant framing
# Guarantees assistant_* frames are broadcast even if the LLM provider fails.
# Text-first design: generate/broadcast assistant text; TTS is optional elsewhere.

from __future__ import annotations
from typing import List, Dict, Tuple, Optional
import base64
import threading, time as _t

from .llm_provider import get_provider
from .awareness import annotate
from .engagement import score as score_engagement
from .dialog_policy import pick as pick_policy
from .retrieval import search as kb_search
from .persona_prompt import build_persona_preamble, format_kb_context
from .suggestions import hygienic_suggestions
from ..db import db
from ..ws.bus import bus

try:
    from ..api_v1.admin import _emit as _admin_emit  # SSE to Admin
except Exception:
    def _admin_emit(*a, **k):  # no-op if admin channel absent
        pass

def _get_persona_for_session(session_id: str) -> Dict:
    try:
        sess = db.memory.get('sessions', {}).get(session_id) or {}
        persona_id = (sess.get('persona_id') or 'chip')
        return db.memory.get('personas', {}).get(persona_id) or {'id':'chip'}
    except Exception:
        return {'id':'chip'}

def _build_prompt(seed_text: str, persona: Dict, kb_snippets: List[str], teacher_move: Optional[str]) -> str:
    parts = [build_persona_preamble(persona)]
    if kb_snippets:
        parts.append(format_kb_context(kb_snippets))
    if teacher_move:
        parts.append(f"Teacher move: {teacher_move}.")
    parts.append(f"User said: {seed_text.strip()}")
    return "\n\n".join(parts)

def make_assistant_frames(seed_text: str,
                          session_id: str,
                          meta: Optional[Dict] = None,
                          correlation_user_msg_id: Optional[str] = None) -> Tuple[str, List[Dict]]:
    """
    Produce assistant frames for a given user seed text using the configured LLM provider.
    ALWAYS returns a frames list that includes at least:
        - one 'assistant_chunk' (with either model text OR a safe fallback), and
        - one 'assistant_end'.
    Also broadcasts frames to the WS bus as they are prepared.
    """
    meta = meta or {}
    cfg = db.get_config()
    persona = _get_persona_for_session(session_id)

    # Awareness + dialog policy
    labels = annotate(seed_text, meta)
    labels['engagement'] = score_engagement(seed_text, meta)
    policy = pick_policy(labels, cfg)
    teacher_move = (policy or {}).get('teacher_move')

    # Light retrieval – safe to fail closed
    kb = []
    try:
        if cfg.get('kb_enabled', False):
            kb = kb_search(seed_text, top_k=int(cfg.get('kb_top_k', 3)))
    except Exception:
        kb = []

    prompt = _build_prompt(seed_text, persona, kb, teacher_move)

    # Choose provider (may raise if OPENAI_API_KEY missing/invalid)
    try:
        provider = get_provider(cfg)
    except Exception as e:
        provider = None
        _admin_emit("llm_provider_error", error=e.__class__.__name__)

    # --- Generate reply (with safe fallback) ---------------------------------
    reply: str
    error_note: Optional[str] = None
    if provider is not None:
        try:
            reply = provider.generate_reply(prompt, persona=persona, teacher_move=teacher_move, context={'kb': kb})
        except Exception as e:
            # Fallback text if provider errors out
            error_note = f"llm_error:{e.__class__.__name__}"
            reply = "Hi! I’m ready to help. (Model is warming up.)"
            _admin_emit("llm_generate_error", error=e.__class__.__name__)
    else:
        error_note = "llm_not_available"
        reply = "Hi! I’m ready to help."

    # --- Build frames --------------------------------------------------------
    turn_id = db.memory.setdefault('turn_seq', 0) + 1
    db.memory['turn_seq'] = turn_id  # simple monotonic id; greet may override externally

    frames: List[Dict] = []

    chunk = {'type': 'assistant_chunk', 'turn_id': str(turn_id), 'text': reply}
    if correlation_user_msg_id:
        chunk['correlation_user_msg_id'] = correlation_user_msg_id
    if error_note:
        chunk['note'] = error_note
    frames.append(chunk)

    # Optional suggestions (respect config flags)
    try:
        if cfg.get('suggestions_enabled', True):
            frames.append({'type': 'suggestions', 'turn_id': str(turn_id), 'items': hygienic_suggestions(reply)})
    except Exception:
        pass

    end_fr = {'type': 'assistant_end', 'turn_id': str(turn_id)}
    if correlation_user_msg_id and 'correlation_user_msg_id' not in end_fr:
        end_fr['correlation_user_msg_id'] = correlation_user_msg_id
    frames.append(end_fr)

    # --- Broadcast -----------------------------------------------------------
    try:
        for fr in frames:
            bus.broadcast(session_id, fr)
            # Trace important milestones to Admin SSE
            if fr.get('type') == 'assistant_chunk':
                _admin_emit('assistant_chunk', session_id=session_id, turn_id=str(turn_id))
            elif fr.get('type') == 'assistant_end':
                _admin_emit('assistant_end', session_id=session_id, turn_id=str(turn_id))
    except Exception:
        pass

    return str(turn_id), frames



# --- NEW: WS TTS scheduling (audio over WS) ----------------------------------
def schedule_tts_audio(session_id: str,
                       text: str,
                       turn_id: str | None = None,
                       correlation_user_msg_id: Optional[str] = None,
                       chunk_bytes: int = 8192,
                       delay_ms: int = 0) -> None:
    """Synthesize TTS for `text` and stream as WS frames.
    Emits frames like:
        { "type":"assistant_audio", "turn_id": turn_id, "audio_chunks":[<b64>, ...] }
    Non-blocking: runs in a background thread.
    """
    if not text:
        return
    cfg = db.get_config()
    feature_audio = bool(cfg.get("feature_audio", True))
    if not feature_audio:
        return

    def _run():
        try:
            # Small pacing delay if requested
            if delay_ms and delay_ms > 0:
                _t.sleep(max(0, delay_ms) / 1000.0)

            # Pick provider (prefer canonical providers.tts; keep fallbacks for safety)
            try:
                from ..providers.tts import get_tts_provider
            except Exception:
                try:
                    from ..services.tts_provider import get_tts_provider  # type: ignore
                except Exception:
                    from ..tts_provider import get_tts_provider  # legacy fallback
            provider = get_tts_provider(cfg or {})

            # Synthesize
            try:
                audio_bytes, _vis = provider.synth(text)
            except Exception:
                # On failure, do nothing (text already rendered)
                return

            # Chunk and broadcast
            mv = memoryview(audio_bytes)
            idx = 0
            max_frames = 256
            sent = 0
            while idx < len(mv):
                # stop early if canceled
                try:
                    from ..ws.bus import bus as _bus_ref
                    if _bus_ref.is_canceled(session_id, str(turn_id) if turn_id else None):
                        break
                except Exception:
                    pass
                part = bytes(mv[idx: idx + chunk_bytes])
                idx += chunk_bytes
                try:
                    b64 = base64.b64encode(part).decode("ascii")
                    fr = {"type": "assistant_audio", "turn_id": str(turn_id) if turn_id else None, "audio_chunks": [b64]}
                    if correlation_user_msg_id:
                        fr["correlation_user_msg_id"] = correlation_user_msg_id
                    bus.broadcast(session_id, fr)
                    sent += 1
                    if sent >= max_frames:
                        break
                except Exception:
                    pass
        finally:
            try:
                _admin_emit('schedule_tts_done', session_id=session_id, turn_id=str(turn_id) if turn_id else None)
            except Exception:
                pass

    threading.Thread(target=_run, daemon=True).start()


def schedule_frames(session_id: str,
                    frames: List[Dict],
                    delay_ms: int = 0,
                    correlation_user_msg_id: Optional[str] = None) -> None:
    """
    Optionally schedule frames with a delay (for pacing). Ensures an assistant_end is emitted.
    Non-blocking: runs in a background thread.
    """
    def _run():
        try:
            if delay_ms and delay_ms > 0:
                _t.sleep(max(0, delay_ms) / 1000.0)
            for fr in frames:
                try:
                    bus.broadcast(session_id, fr)
                except Exception:
                    pass
            # Ensure an assistant_end terminator exists
            if not any(fr.get('type') in ('assistant_end', 'end') for fr in frames):
                end_fr = {'type': 'assistant_end', 'turn_id': frames[-1].get('turn_id') if frames else None}
                if correlation_user_msg_id and 'correlation_user_msg_id' not in end_fr:
                    end_fr['correlation_user_msg_id'] = correlation_user_msg_id
                try:
                    bus.broadcast(session_id, end_fr)
                except Exception:
                    pass
        finally:
            try:
                _admin_emit('schedule_frames_done', session_id=session_id)
            except Exception:
                pass

    threading.Thread(target=_run, daemon=True).start()


# ---- WS orchestration helpers (centralize greet/user pipelines) ----

def run_ws_greet(session_id: str) -> str:
    """
    Produce assistant text for greet, broadcast frames, schedule TTS audio,
    and nudge UI with state+suggestions. Returns turn_id.
    """
    tid, frames = make_assistant_frames("greet", session_id, meta={"source": "ws_greet"})
    # TTS: use first assistant_chunk text if present (feature_audio gating inside schedule_tts_audio)
    try:
        text_for_tts = next((fr.get("text") for fr in frames if fr.get("type") == "assistant_chunk"), "")
        if text_for_tts:
            schedule_tts_audio(session_id, text_for_tts, turn_id=tid)
    except Exception:
        pass
    # UI nudges
    try:
        bus.broadcast(session_id, {"type": "state", "phase": "ready"})
        bus.broadcast(session_id, {"type": "suggestions", "turn_id": tid, "items": hygienic_suggestions("")})
    except Exception:
        pass
    return tid


def run_ws_user_turn(session_id: str, text: str, correlation_user_msg_id: Optional[str] = None) -> str:
    """
    Produce assistant text for a user turn and schedule both text pacing and TTS.
    Mirrors HTTP /api_v1/chat behavior for consistency.
    """
    tid, frames = make_assistant_frames(text, session_id, meta={"source": "user_ws"},
                                        correlation_user_msg_id=correlation_user_msg_id)
    # Keep schedule_frames for pacing/end guarantees to mirror HTTP route
    try:
        schedule_frames(session_id, frames, correlation_user_msg_id=correlation_user_msg_id)
    except Exception:
        pass
    # TTS for assistant text
    try:
        text_for_tts = next((fr.get("text") for fr in frames if fr.get("type") == "assistant_chunk"), "")
        if text_for_tts:
            schedule_tts_audio(session_id, text_for_tts, turn_id=tid, correlation_user_msg_id=correlation_user_msg_id)
    except Exception:
        pass
    return tid
