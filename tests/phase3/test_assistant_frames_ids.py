
import base64, time, threading
from app.ws.bus import bus

def _make_frames(tid):
    return [
        {"type":"assistant_chunk","turn_id":tid,"text":"hello"},
        {"type":"audio_chunk","turn_id":tid,"format":"mp3","base64":"QQ=="},
        {"type":"assistant_end","turn_id":tid},
    ]

def _schedule(session_id, frames, delay_ms: int, correlation_user_msg_id):
    def run():
        for fr in frames:
            if correlation_user_msg_id and 'correlation_user_msg_id' not in fr:
                fr['correlation_user_msg_id'] = correlation_user_msg_id
            bus.broadcast(session_id, fr)
            time.sleep(delay_ms/1000.0)
    threading.Thread(target=run, daemon=True).start()

def test_frames_have_turn_and_correlation_ids():
    sid = "p3-corr"
    tid = "tid-77"
    q = bus.subscribe(sid)
    frames = _make_frames(tid)
    _schedule(sid, frames, delay_ms=1, correlation_user_msg_id="user-msg-001")
    seen = [q.get(timeout=1.0) for _ in range(3)]
    assert all(fr.get("turn_id")==tid for fr in seen)
    assert all(fr.get("correlation_user_msg_id")=="user-msg-001" for fr in seen)
