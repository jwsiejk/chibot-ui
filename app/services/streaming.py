# app/services/streaming.py — Phase 7: LLM provider + persona prompt; assistant_* frames
from __future__ import annotations
from typing import List, Dict, Tuple, Optional
import threading, time

from .llm_provider import get_provider
from .awareness import annotate
from .engagement import score as score_engagement
from .dialog_policy import pick as pick_policy
from .retrieval import search as kb_search
from .persona_prompt import build_persona_preamble, format_kb_context
from .suggestions import hygienic_suggestions
from ..db import db
from ..ws.bus import bus

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

def make_assistant_frames(seed_text: str, session_id: str, meta: Dict | None = None, correlation_user_msg_id: Optional[str] = None) -> List[Dict]:
    """Produce assistant frames for a given user seed text using the configured LLM provider.
    Frames use the Phase 7 schema: 'assistant_chunk' then 'assistant_end' (optionally 'suggestions').
    Returns the list of frames and also broadcasts them onto the WS bus.
    """
    meta = meta or {}
    cfg = db.get_config()
    provider = get_provider(cfg)
    persona = _get_persona_for_session(session_id)
    # Awareness + dialog policy
    labels = annotate(seed_text, meta)
    labels['engagement'] = score_engagement(seed_text, meta)
    policy = pick_policy(labels, cfg)
    teacher_move = policy.get('teacher_move')

    # Retrieval (optional; offline-friendly)
    kb = kb_search(seed_text, top_k=3) if hasattr(kb_search, '__call__') else []

    # Prompt build
    prompt = _build_prompt(seed_text, persona, kb, teacher_move)
    turn_id = getattr(provider, 'new_turn_id', lambda: str(int(time.time()*1000)))()

    # Generate single reply (non-stream for offline tests)
    reply = provider.generate_reply(prompt, persona=persona, teacher_move=teacher_move, context={'kb': kb})

    frames: List[Dict] = []
    # assistant_chunk
    chunk = {'type': 'assistant_chunk', 'turn_id': turn_id, 'text': reply}
    if correlation_user_msg_id:
        chunk['correlation_user_msg_id'] = correlation_user_msg_id
    frames.append(chunk)

    # optional suggestions (respect config flags)
    try:
        if cfg.get('suggestions_enabled', True):
            frames.append({'type': 'suggestions', 'turn_id': turn_id, 'items': hygienic_suggestions(reply)})
    except Exception:
        pass

    # assistant_end
    end_fr = {'type': 'assistant_end', 'turn_id': turn_id}
    if correlation_user_msg_id and 'correlation_user_msg_id' not in end_fr:
        end_fr['correlation_user_msg_id'] = correlation_user_msg_id
    frames.append(end_fr)

    # Broadcast
    try:
        for fr in frames:
            bus.broadcast(session_id, fr)
    except Exception:
        pass
    return frames

def schedule_frames(session_id: str, frames: List[Dict], delay_ms: int = 0, correlation_user_msg_id: Optional[str] = None):
    """Enqueue frames over time onto the WS bus (used by greet)."""
    def _run():
        import time as _t
        for fr in frames:
            if correlation_user_msg_id and 'correlation_user_msg_id' not in fr:
                fr['correlation_user_msg_id'] = correlation_user_msg_id
            try:
                bus.broadcast(session_id, fr)
            except Exception:
                pass
            _t.sleep(max(0, delay_ms)/1000.0)
        # Ensure an assistant_end terminator exists
        if not any(fr.get('type') in ('assistant_end','end') for fr in frames):
            end_fr = {'type': 'assistant_end', 'turn_id': frames[-1].get('turn_id') if frames else None}
            if correlation_user_msg_id and 'correlation_user_msg_id' not in end_fr:
                end_fr['correlation_user_msg_id'] = correlation_user_msg_id
            try:
                bus.broadcast(session_id, end_fr)
            except Exception:
                pass
    threading.Thread(target=_run, daemon=True).start()
