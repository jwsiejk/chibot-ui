
import time, base64, threading
from app.ws.bus import bus
from queue import Empty

def _make_audio_frames(turn_id: str, n=24):
    frames = [{"type":"assistant_chunk","turn_id":turn_id,"text":"speaking..."}]
    chunk = base64.b64encode(b"A"*64).decode("ascii")
    for i in range(n):
        frames.append({"type":"audio_chunk","turn_id":turn_id,"format":"mp3","base64": chunk})
    frames.append({"type":"assistant_end","turn_id":turn_id})
    return frames

def _schedule_frames(session_id, frames, delay_ms: int, correlation_user_msg_id=None):
    def run():
        import time as _t
        for fr in frames:
            if correlation_user_msg_id and 'correlation_user_msg_id' not in fr:
                fr['correlation_user_msg_id'] = correlation_user_msg_id
            try: bus.broadcast(session_id, fr)
            except Exception: pass
            _t.sleep(max(0, delay_ms)/1000.0)
    threading.Thread(target=run, daemon=True).start()

def test_tts_abort_drops_future_audio_chunks():
    sid = "p3-tts-abort"
    tid = "turn-p3"
    q = bus.subscribe(sid)

    frames = _make_audio_frames(tid, n=40)
    _schedule_frames(sid, frames, delay_ms=15, correlation_user_msg_id="u-msg-77")

    got_audio = 0
    committed = False
    import time
    deadline = time.time() + 3.0

    while time.time() < deadline:
        try: fr = q.get(timeout=0.2)
        except Empty: break
        if fr.get("type") == "audio_chunk" and fr.get("turn_id") == tid:
            got_audio += 1
            if got_audio == 6 and not committed:
                bus.cancel_turn(sid, tid)
                committed = True
        if committed and fr.get("type") == "assistant_end" and fr.get("turn_id") == tid:
            assert False, "assistant_end should be dropped after cancel_turn"
        if committed and got_audio > 8:
            break

    assert committed, "Commit path not exercised"
    assert got_audio < 40, f"Abort failed; received all {got_audio} chunks"
