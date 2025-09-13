
from app.services.streaming import schedule_frames
from app.ws.bus import bus

def test_correlation_id_propagates_on_frames():
    sid = "p1-corr"
    q = bus.subscribe(sid)
    frames = [
        {"type":"assistant_chunk","turn_id":"tid-corr","text":"hello"},
        {"type":"audio_chunk","turn_id":"tid-corr","format":"mp3","base64":"QQ=="},
        {"type":"assistant_end","turn_id":"tid-corr"},
    ]
    schedule_frames(sid, frames, delay_ms=1, correlation_user_msg_id="user-msg-001")
    seen = []
    for _ in range(3):
        fr = q.get(timeout=1.0)
        seen.append(fr)
    # Every frame should carry the correlation id
    assert all(fr.get("correlation_user_msg_id") == "user-msg-001" for fr in seen), seen
