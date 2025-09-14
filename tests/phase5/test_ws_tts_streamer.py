
import time
from app.ws.bus import bus
from app.services.streaming import schedule_tts_audio

def test_ws_tts_streamer_chunks_and_aborts():
    sid = "p5-tts"
    tid = "turn-5"
    q = bus.subscribe(sid)

    # 40KB payload => ~5 chunks at 8KB
    payload = b"A" * (8*1024*5)

    schedule_tts_audio(sid, text="hello", turn_id=tid, correlation_user_msg_id="u5", audio_bytes=payload, chunk_bytes=8192, delay_ms=5)

    got_audio = 0
    saw_end_before_cancel = False
    canceled = False
    deadline = time.time() + 3.0

    while time.time() < deadline:
        try:
            fr = q.get(timeout=0.5)
        except Exception:
            if canceled:
                break
            else:
                continue
        if fr.get("turn_id") != tid:
            continue
        if fr.get("type") == "audio_chunk":
            got_audio += 1
            if got_audio == 3 and not canceled:
                bus.cancel_turn(sid, tid)
                canceled = True
        elif fr.get("type") == "assistant_end":
            saw_end_before_cancel = True
            break

    assert got_audio < 6, f"Expected early stop after cancel, got {got_audio} chunks"
    assert not saw_end_before_cancel, "assistant_end should be dropped after cancel"
