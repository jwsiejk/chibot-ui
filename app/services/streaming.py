# app/services/streaming.py
from typing import List, Dict, Tuple
import base64, threading, time

from .llm_provider import get_provider
from .awareness import annotate
from .engagement import score as score_engagement
from .dialog_policy import pick as pick_policy
from .retrieval import search as kb_search
from .persona_prompt import build_persona_preamble
from .suggestions import hygienic_suggestions
from .tts_provider import get_tts_provider
from ..db import db

from ..db import db
from ..ws.bus import bus

def _collect_st_memory(session_id: str, turns: int = 6) -> str:
    try:
        from ..dal import neon_pg
        msgs = neon_pg.list_messages(session_id)
    except Exception:
        # in-memory fallback
        msgs = [{"role": r, "text": t, "created_at": 0}
                for (r,t) in db.memory.get("sessions",{}).get(session_id or "default",{}).get("messages",[])]
    if not msgs: return ""
    tail = msgs[-turns:]
    lines = []
    for m in tail:
        role = m.get("role","user")
        text = m.get("text") or m.get("content") or ""
        lines.append(f"{role.upper()}: {text}")
    return "\n".join(lines)

def _maybe_summarize(session_id: str, text: str) -> str:
    if not text: return ""
    parts = [p.strip() for p in text.splitlines() if p.strip()]
    keep = parts[:5]
    return "Recent context summary: " + " / ".join(keep)

def make_assistant_frames(seed_text: str, session_id: str | None = None, meta: dict | None = None) -> Tuple[str, List[Dict]]:
    """Compose an assistant reply (NLG), synthesize MP3+visemes (TTS),
    and return frames for WS streaming (assistant_chunk, visemes, audio_chunk*, suggestions, assistant_end)."""
    cfg_db = db.get_config()
    provider = get_provider(cfg_db)

    # Persona + policy
    ann = annotate((seed_text or ""), (meta or {}))
    persona_id = db.memory.get('sessions',{}).get(session_id or 'default',{}).get('persona_id','chip')
    persona = db.memory.get('personas',{}).get(persona_id, {'id':'chip'})

    # Context: KB + short-term memory
    kb = kb_search(seed_text or "", limit=3)
    preamble = build_persona_preamble(persona)
    stn = int(cfg_db.get('short_term_window', 6))
    st_text = _collect_st_memory(session_id, stn)
    st_summary = _maybe_summarize(session_id, st_text) if cfg_db.get('short_term_summary', True) else ''
    labels = score_engagement(seed_text or '', meta or {})
    policy  = pick_policy(labels, cfg_db)

    context = {
        'session_id': session_id, 'kb': kb, 'preamble': preamble,
        'st_memory': st_text, 'st_summary': st_summary,
        'labels': labels, 'policy': policy
    }

    # NLG
    tid = provider.new_turn_id()
    reply = provider.generate_reply(seed_text or "Hello", persona=persona,
                                    teacher_move=policy.get('teacher_move') or ann.get('teacher_move'),
                                    context=context)

    # TTS (MP3 + visemes)
    settings = cfg_db
    audio_on = settings.get('feature_audio', True)
    a_bytes, vis = (b'', [])
    if audio_on:
        a_bytes, vis = get_tts_provider(cfg_db).synth(reply)
    audio_b64 = base64.b64encode(a_bytes).decode("ascii")
    chunk_size = 32768
    b64_chunks = [audio_b64[i:i+chunk_size] for i in range(0, len(audio_b64), chunk_size)]

    frames: List[Dict] = []
    frames.append({"type":"state","phase":"assistant_speaking","turn_id":tid})
    frames.append({"type":"assistant_chunk","turn_id":tid,"text":reply})
    frames.append({"type":"visemes","turn_id":tid,"items": vis})
    for c in b64_chunks:
        frames.append({"type":"audio_chunk","turn_id":tid,"format":"mp3","base64": c})
    frames.append({"type":"suggestions","turn_id":tid,"items": hygienic_suggestions(reply)})
    frames.append({"type":"assistant_end","turn_id":tid})
    return tid, frames

def schedule_frames(session_id: str, frames: List[Dict], delay_ms: int = 120, **kw):
    def run():
        for fr in frames:
            try:
                bus.broadcast(session_id, fr)
            except Exception:
                pass
            time.sleep(max(0, delay_ms)/1000.0)
        ( _arm_nudge_after_end(session_id, frames) if kw.get('enable_nudge', True) else None )
    threading.Thread(target=run, daemon=True).start()

def _arm_nudge_after_end(session_id: str, frames: list):
    try:
        from ..policy.nudges import arm_nudge
        if any((fr.get("type") in ("assistant_end","end")) for fr in frames):
            arm_nudge(session_id)
    except Exception:
        pass

def make_assistant_frames_text_only(seed_text: str, session_id: str | None = None, meta: dict | None = None) -> Tuple[str, List[Dict]]:
    cfg_db = db.get_config()
    provider = get_provider(cfg_db)
    persona_id = db.memory.get('sessions',{}).get(session_id or 'default',{}).get('persona_id','chip')
    persona = db.memory.get('personas',{}).get(persona_id, {'id':'chip'})
    kb = kb_search((seed_text or ""), limit=2)
    preamble = build_persona_preamble(persona)
    context = {'session_id': session_id, 'kb': kb, 'preamble': preamble}
    tid = provider.new_turn_id()
    reply = provider.generate_reply((seed_text or ""), persona=persona, teacher_move=None, context=context)
    frames = [
        {"type":"assistant_chunk","turn_id":tid,"text": reply},
        {"type":"suggestions","turn_id":tid,"items": hygienic_suggestions(reply)},
        {"type":"assistant_end","turn_id":tid}
    ]
    return tid, frames
