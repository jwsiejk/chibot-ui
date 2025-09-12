from starlette.testclient import TestClient
from app.asgi_gateway import asgi

def test_root_get_ok_and_headers():
    c = TestClient(asgi)
    r = c.get("/")
    assert r.status_code == 200
    assert "Ask Chip" in r.text
    # Security headers present
    h = r.headers
    assert h.get("X-Content-Type-Options") == "nosniff"
    assert h.get("X-Frame-Options") == "DENY"
    assert "Content-Security-Policy" in h
