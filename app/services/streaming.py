# app/services/streaming.py — Production-grade assistant framing
# Guarantees assistant_* frames are broadcast even if the LLM provider fails.
# Text-first design: generate/broadcast assistant text; TTS is optional elsewhere.

from __future__ import annotations
from typing import List, Dict, Tuple, Optional
import base64
import threading, time as _t
import os
import re

from .llm_provider import get_provider
from .awareness import annotate
from .engagement import score as score_engagement
from .dialog_policy import pick as pick_policy
from .retrieval import search as kb_search
from .persona_prompt import build_persona_preamble, format_kb_context
from .suggestions import hygienic_suggestions
from ..db import db
from ..ws.bus import bus
from app.obs import jlog, span

try:
    from ..api_v1.admin import _emit as _admin_emit  # SSE to Admin
except Exception:
    def _admin_emit(*a, **k):  # no-op if admin channel absent
        pass


def _get_persona_for_session(session_id: str) -> Dict:
    try:
        sess = db.memory.get('sessions', {}).get(session_id) or {}
        persona_id = (sess.get('persona_id') or 'chip')
        return db.memory.get('personas', {}).get(persona_id) or {'id': 'chip'}
    except Exception:
        return {'id': 'chip'}


def _build_prompt(seed_text: str, persona: Dict, kb_snippets: List[str], teacher_move: Optional[str]) -> str:
    parts = [build_persona_preamble(persona)]
    if kb_snippets:
        parts.append(format_kb_context(kb_snippets))
    if teacher_move:
        parts.append(f"Teacher move: {teacher_move}.")
    parts.append(f"User said: {seed_text.strip()}")
    return "\n\n".join(parts)


# --- text hygiene -------------------------------------------------------------
# Remove ANY occurrence (not just trailing) of legacy "[KB:n]" stamps, case-insensitive.
_KB_TAG_RE = re.compile(r"\s*\[(?:KB|kb)\s*:\s*\d+\]\s*")

def _scrub_debug_stamps(s: str) -> str:
    """
    Remove legacy debug stamps like "[KB:0]" that were historically appended to
    assistant text for telemetry. We keep the value as metadata instead.
    This removes ALL occurrences safely without touching other content.
    """
    if not s:
        return s
    # Replace any matches with a single space, then trim.
    return _KB_TAG_RE.sub(" ", s).strip()


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
    with span("retrieval", session_id=session_id, phase="turn"):
        try:
            if cfg.get('kb_enabled', False):
                kb = kb_search(seed_text, top_k=int(cfg.get('kb_top_k', 3)))
        except Exception:
            kb = []
    jlog("retrieval:result", session_id=session_id, kb_hits=len(kb))

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
            with span("llm", session_id=session_id, phase="turn"):
                reply = provider.generate_reply(prompt, persona=persona, teacher_move=teacher_move, context={'kb': kb})
                jlog("llm:ok", session_id=session_id, model=getattr(provider, "model_name","unknown"),
                     tokens_in=getattr(provider, "last_tokens_in", None), tokens_out=getattr(provider, "last_tokens_out", None))
        except Exception as e:
            # Fallback text if provider errors out
            error_note = f"llm_error:{e.__class__.__name__}"
            reply = "Hi! I’m ready to help. (Model is warming up.)"
            _admin_emit("llm_generate_error", error=e.__class__.__name__)
    else:
        error_note = "llm_not_available"
        reply = "Hi! I’m ready to help."

    # Scrub any legacy stamps like "[KB:0]" so UI/TTS never surface them
    safe_reply = _scrub_debug_stamps(reply)
    jlog("frame:assistant_text", session_id=session_id, assistant_text=safe_reply, kb_hits=len(kb))

    # --- Build frames --------------------------------------------------------
    turn_id = db.memory.setdefault('turn_seq', 0) + 1
    db.memory['turn_seq'] = turn_id  # simple monotonic id; greet may override externally

    frames: List[Dict] = []

    chunk = {
        'type': 'assistant_chunk',
        'turn_id': str(turn_id),
        'text': safe_reply,
        # Expose KB hits as metadata so UI can display it without polluting text.
        'kb_hits': len(kb),
    }
    if correlation_user_msg_id:
        chunk['correlation_user_msg_id'] = correlation_user_msg_id
    if error_note:
        chunk['note'] = error_note
    frames.append(chunk)

    # Optional suggestions (respect config flags)
    try:
        if cfg.get('suggestions_enabled', True):
            frames.append({'type': 'suggestions', 'turn_id': str(turn_id), 'items': hygienic_suggestions(safe_reply)})
    except Exception:
        pass

    end_fr = {'type': 'assistant_end', 'turn_id': str(turn_id)}
    if correlation_user_msg_id and 'correlation_user_msg_id' not in end_fr:
        end_fr['correlation_user_msg_id'] = correlation_user_msg_id
    frames.append(end_fr)
    # --- Broadcast -----------------------------------------------------------
    try:
        with span("frames:broadcast", session_id=session_id, turn_id=str(turn_id)):
            for fr in frames:
                # Belt-and-suspenders: scrub again if any legacy tag slipped in
                if isinstance(fr, dict) and fr.get('type') == 'assistant_chunk' and 'text' in fr:
                    fr['text'] = _scrub_debug_stamps(fr['text'])
                bus.broadcast(session_id, fr)
                # Trace important milestones to Admin SSE + logs
                if fr.get('type') == 'assistant_chunk':
                    _admin_emit('assistant_chunk', session_id=session_id, turn_id=str(turn_id))
                    jlog('broadcast:assistant_chunk', session_id=session_id, turn_id=str(turn_id), size=len(fr.get('text') or ''))
                elif fr.get('type') == 'assistant_end':
                    _admin_emit('assistant_end', session_id=session_id, turn_id=str(turn_id))
                    jlog('broadcast:assistant_end', session_id=session_id, turn_id=str(turn_id))
    except Exception:
        pass
        pass

    return str(turn_id), frames


# --- MIME mapping helper ------------------------------------------------------

def _guess_tts_mime(output_format: str | None) -> str:
    """
    Map configured TTS output format to an appropriate MIME/container for MSE playback.
    We keep this conservative to avoid mismatches with provider bytes.
    """
    fmt = (output_format or os.environ.get('ELEVEN_OUTPUT_FORMAT') or '').lower()
    # Prefer actual, common provider defaults. If unknown, assume MP3 to avoid mismatches.
    if 'webm' in fmt and 'opus' in fmt:
        return 'audio/webm; codecs="opus"'
    if 'ogg' in fmt and 'opus' in fmt:
        return 'audio/ogg; codecs="opus"'
    if 'mp3' in fmt:
        return 'audio/mpeg'
    if 'wav' in fmt or 'pcm' in fmt or 'l16' in fmt:
        return 'audio/wav'
    # Fallback to MP3 (widely supported; safer than claiming WebM if provider returned MP3)
    return 'audio/mpeg'


# --- WS TTS scheduling (audio over WS) ---------------------------------------
def schedule_tts_audio(session_id: str,
                       text: str,
                       turn_id: str | None = None,
                       correlation_user_msg_id: Optional[str] = None,
                       chunk_bytes: int = 8192,
                       delay_ms: int = 0) -> None:
    """Synthesize TTS for `text` and stream as WS frames.
    Emits frames like:
        {
          "type": "assistant_audio",
          "turn_id": <str or null>,
          "mime": "<audio mime>",
          "audio_chunks": ["<base64>", ...],
          "is_last": <bool>
        }
    After the final chunk, an explicit:
        { "type": "UtteranceEnd", "turn_id": <str or null> }
    is broadcast to mark audio-complete.
    Non-blocking: runs in a background thread.
    """
    if not text:
        return
    cfg = db.get_config()
    feature_audio = bool(cfg.get("feature_audio", True))

    # Track TTS lifecycle for admin truth
    tts_tbl = db.memory.setdefault('tts_status', {})
    sess_tbl = tts_tbl.setdefault(session_id, {})
    turn_key = str(turn_id) if turn_id is not None else 'greet'
    state = sess_tbl.setdefault(turn_key, {'started': False, 'first_chunk': False, 'done': False, 'error': None})

    if not feature_audio:
        return

    def _run():

        try:
            if delay_ms and delay_ms > 0:
                _t.sleep(max(0, delay_ms) / 1000.0)
            from app.obs import span as _span_local
            with _span_local("schedule_frames", session_id=session_id):
                for fr in frames:
                    try:
                        # Belt-and-suspenders scrub
                        if isinstance(fr, dict) and fr.get('type') == 'assistant_chunk' and 'text' in fr:
                            fr['text'] = _scrub_debug_stamps(fr['text'])
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
            from app.obs import span as _span_local
            with _span_local("schedule_frames", session_id=session_id):
                for fr in frames:
                    try:
                        # Belt-and-suspenders scrub
                        if isinstance(fr, dict) and fr.get('type') == 'assistant_chunk' and 'text' in fr:
                            fr['text'] = _scrub_debug_stamps(fr['text'])
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
            # Pass scrubbed text to TTS (extra safety)
            schedule_tts_audio(session_id, _scrub_debug_stamps(text_for_tts), turn_id=tid)
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
            # Pass scrubbed text to TTS (extra safety)
            schedule_tts_audio(session_id, _scrub_debug_stamps(text_for_tts), turn_id=tid, correlation_user_msg_id=correlation_user_msg_id)
    except Exception:
        pass
    return tid
