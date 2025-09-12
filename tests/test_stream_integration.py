
from starlette.testclient import TestClient
from app.asgi_gateway import asgi
import time

def test_stream_route_emits_partials_and_final():
    client = TestClient(asgi)
    # Send ~6 chunks
    for i in range(6):
        data = b'X' * 100
        r = client.post("/api/v1/voice/stt/stream?session_id=t1", content=data, headers={"content-type": "application/octet-stream"})
        assert r.status_code == 200
    # Allow background loop to emit
    time.sleep(0.2)
    # Poll events
    events = client.get("/api/v1/_test/events?session_id=t1").json()
    joined = " ".join([str(e) for e in events])
    assert "user_partial" in joined
    assert "user_final" in joined

def test_stream_route_size_limit():
    client = TestClient(asgi)
    big = b'X' * (512*1024 + 1)
    r = client.post("/api/v1/voice/stt/stream?session_id=t2", content=big, headers={"content-type": "application/octet-stream"})
    assert r.status_code == 413
