from typing import List, Dict, Tuple
import base64, threading, time

from .llm_provider import get_provider
from .awareness import annotate
from .retrieval import search as kb_search
from .persona_prompt import build_persona_preamble
from .suggestions import hygienic_suggestions

from ..db import db
from ..ws.bus import bus
from .tts_provider import get_tts_provider

def make_assistant_frames(seed_text: str, session_id: str | None = None, meta: dict | None = None) -> Tuple[str, List[Dict]]:
    """Compose an assistant reply (NLG), synthesize MP3+visemes (TTS),
    and return a list of frames suitable for WS streaming.
    Emits: assistant_chunk, visemes, audio_chunk*, suggestions, assistant_end
    """
    # Provider selection
    cfg = db.get_config()
    provider = get_provider(cfg)

    # Persona + awareness
    ann = annotate((seed_text or ""), (meta or {}))
    persona_id = db.memory.get('sessions',{}).get(session_id or 'default',{}).get('persona_id','chip')
    persona = db.memory.get('personas',{}).get(persona_id, {'id':'chip'})

    # Light retrieval context
    kb = kb_search(seed_text or "", limit=3)
    preamble = build_persona_preamble(persona)
    context = {'session_id': session_id, 'kb': kb, 'preamble': preamble}

    # NLG
    tid = provider.new_turn_id()
    reply = provider.generate_reply(seed_text or "Hello", persona=persona, teacher_move=ann.get('teacher_move'), context=context)

    # TTS (MP3 + visemes)
    a_bytes, vis = get_tts_provider(cfg).synth(reply)
    audio_b64 = base64.b64encode(a_bytes).decode("ascii")
    # chunk b64 into ~32KB for transport
    chunk_size = 32768
    b64_chunks = [audio_b64[i:i+chunk_size] for i in range(0, len(audio_b64), chunk_size)]

    frames: List[Dict] = []
    # Optional state frame (harmless)
    frames.append({"type":"state","phase":"assistant_speaking","turn_id":tid})
    # Text + visemes early so UI can prepare
    frames.append({"type":"assistant_chunk","turn_id":tid,"text":reply})
    frames.append({"type":"visemes","turn_id":tid,"items": vis})

    for c in b64_chunks:
        frames.append({"type":"audio_chunk","turn_id":tid,"format":"mp3","base64": c})

    # Suggestions (≤4 chips, ≤7 words handled inside hygienic_suggestions)
    frames.append({"type":"suggestions","turn_id":tid,"items": hygienic_suggestions(reply)})

    # End marker
    frames.append({"type":"assistant_end","turn_id":tid})

    return tid, frames

def schedule_frames(session_id: str, frames: List[Dict], delay_ms: int = 120):
    """Broadcast frames to the WS bus with minimal pacing."""
    def run():
        for fr in frames:
            try:
                bus.broadcast(session_id, fr)
            except Exception:
                pass
            time.sleep(max(0, delay_ms)/1000.0)
        _arm_nudge_after_end(session_id, frames)
    t = threading.Thread(target=run, daemon=True)
    t.start()

# After frames are streamed, arm a nudge on assistant_end
def _arm_nudge_after_end(session_id: str, frames: list):
    try:
        from ..policy.nudges import arm_nudge
        if any((fr.get("type") in ("assistant_end","end")) for fr in frames):
            arm_nudge(session_id)
    except Exception:
        # avoid crashing stream thread
        pass
