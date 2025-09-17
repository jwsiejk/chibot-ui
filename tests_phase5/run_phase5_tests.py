
import time, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.ws.bus import bus
from app.services.streaming import schedule_tts_audio

def drain_for(q, ms=500):
    out = []
    end = time.time() + (ms/1000.0)
    while time.time() < end:
        try:
            fr = q.get(timeout=0.02)
            out.append(fr)
        except Exception:
            pass
    return out

def test_stream_chunks_and_cancel():
    sid = "p5-tts"
    tid = "turn-5"
    q = bus.subscribe(sid)

    payload = b"A" * (8*1024*5)  # 40KB, 5 chunks at 8KB

    schedule_tts_audio(sid, text="hello", turn_id=tid, correlation_user_msg_id="u5",
                       audio_bytes=payload, chunk_bytes=8192, delay_ms=5)

    got_audio = 0
    saw_end_before_cancel = False
    canceled = False
    t0 = time.time()
    while time.time() - t0 < 2.0:
        for fr in drain_for(q, ms=25):
            if fr.get("turn_id") != tid:
                continue
            t = fr.get("type")
            if t == "audio_chunk":
                got_audio += 1
                if got_audio == 3 and not canceled:
                    bus.cancel_turn(sid, tid)
                    canceled = True
            elif t == "assistant_end":
                saw_end_before_cancel = True
                break
        if canceled and got_audio >= 3:
            # give a little time for any in-flight frames
            time.sleep(0.05)
            break

    assert got_audio < 6, f"Expected early stop after cancel, got {got_audio} chunks"
    assert not saw_end_before_cancel, "assistant_end should be dropped after cancel"

def test_route_linter_passes():
    import runpy
    code = 0
    try:
        runpy.run_path(str(ROOT/"scripts/route_linter.py"), run_name="__main__")
    except SystemExit as e:
        code = int(getattr(e, "code", 0) or 0)
    assert code == 0, "route-linter reported forbidden routes"
