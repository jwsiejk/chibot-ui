import sys, pathlib, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.ws.bus import bus

def test_bus_queues_then_flushes():
    sid = "test-sid"
    # Broadcast before any subscriber
    bus.broadcast(sid, {"type":"assistant_chunk","turn_id":"t1","text":"hi"})
    # Now subscribe and expect queued frame
    q = bus.subscribe(sid)
    got = q.get(timeout=1.0)
    assert got.get("type") == "assistant_chunk" and got.get("text") == "hi"