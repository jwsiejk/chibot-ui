
from app.asgi_gateway import asgi
from starlette.testclient import TestClient

def test_no_legacy_routes():
    client = TestClient(asgi)
    # Positive check: admin config exists
    r = client.get("/api/v1/admin/config")
    assert r.status_code == 200
    # Negative: no legacy greet or non-v1 tts
    for bad in ["/api/greet", "/api/v1/voice/tts"]:
        resp = client.get(bad)
        assert resp.status_code in (404, 405)
