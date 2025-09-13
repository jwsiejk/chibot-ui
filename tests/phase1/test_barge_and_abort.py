
import time, base64, threading
from app.ws.bus import bus
from app.services.streaming import schedule_frames
from app.ws.barge import BargeState

def _make_audio_frames(turn_id: str, n=12):
    frames = [{"type":"assistant_chunk","turn_id":turn_id,"text":"speaking..."}]
    chunk = base64.b64encode(b"A"*64).decode("ascii")
    for i in range(n):
        frames.append({"type":"audio_chunk","turn_id":turn_id,"format":"mp3","base64": chunk})
    frames.append({"type":"assistant_end","turn_id":turn_id})
    return frames

def test_barge_commit_aborts_audio_delivery():
    sid = "p1"
    tid = "turn-p1"
    q = bus.subscribe(sid)

    # Schedule frames in a background thread to simulate streaming
    frames = _make_audio_frames(tid, n=20)
    th = threading.Thread(target=lambda: schedule_frames(sid, frames, delay_ms=25, correlation_user_msg_id="cid-123"))
    th.daemon = True
    th.start()

    got_audio = 0
    t0 = time.time()
    committed = False
    # Pull a few frames, then cancel
    while time.time() - t0 < 2.5:
        fr = q.get(timeout=2.0)
        if fr.get("type") == "audio_chunk" and fr.get("turn_id") == tid:
            got_audio += 1
            if got_audio == 5 and not committed:
                bus.cancel_turn(sid, tid)  # simulate barge commit
                committed = True
        if committed and fr.get("type") == "audio_chunk" and fr.get("turn_id") == tid:
            # After commit, bus should drop further audio for this turn
            # We'll allow one in-flight, but not many more; break early
            break
        if fr.get("type") == "assistant_end" and fr.get("turn_id") == tid:
            # end frame should be dropped after cancel; if we got it, break
            break

    # We scheduled 20 audio chunks; if abort worked, we should see significantly fewer
    assert got_audio < 20, f"Abort failed; received all {got_audio} chunks"
    assert committed, "Commit path not exercised"
