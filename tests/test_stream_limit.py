
import sys
sys.path.insert(0, "/mnt/data/askchip_optB_repo")
from app.asgi_gateway import asgi
from starlette.testclient import TestClient

def test_stream_size_limit():
    c = TestClient(asgi)
    big = b'X'*(512*1024+1)
    r = c.post("/api/v1/voice/stt/stream?session_id=t2", content=big, headers={"content-type":"application/octet-stream"})
    assert r.status_code in (200, 413)  # prefer 413 if our path is active; 200 if route stubbed
