from typing import List, Dict, Tuple
from .mock_llm import new_turn_id, generate_reply
from .mock_tts import synth
from .suggestions import hygienic_suggestions
import threading, time
from ..ws.bus import bus

def make_assistant_frames(seed_text: str) -> Tuple[str, List[Dict]]:
    tid = new_turn_id()
    reply = generate_reply(seed_text)
    audio_b64, _ = synth(reply)
    chunks = [audio_b64[i:i+8] for i in range(0, len(audio_b64), 8)][:3]
    frames: List[Dict] = []
    frames.append({"type":"state","phase":"assistant_speaking","turn_id":tid})
    frames.append({"type":"text","role":"assistant","turn_id":tid,"content":reply})
    for c in chunks:
        frames.append({"type":"audio_chunk","turn_id":tid,"codec":"audio/webm;codecs=opus","data":c})
    frames.append({"type":"suggestions","turn_id":tid,"items": hygienic_suggestions()})
    frames.append({"type":"end","turn_id":tid})
    frames.append({"type":"state","phase":"ready"})
    return tid, frames

def schedule_frames(session_id: str, frames: list, delay_ms: int = 30):
    def run():
        for fr in frames:
            bus.broadcast(session_id, fr)
            time.sleep(max(0, delay_ms)/1000.0)
    threading.Thread(target=run, daemon=True).start()


# Phase 2: after frames are streamed, arm a nudge on assistant_end
def _arm_nudge_after_end(session_id: str, frames: list):
    try:
        from ..policy.nudges import arm_nudge
        # Arm only if an 'end' frame exists
        if any((fr.get("type") == "end") for fr in frames):
            arm_nudge(session_id)
    except Exception as e:
        # avoid crashing stream thread
        pass
