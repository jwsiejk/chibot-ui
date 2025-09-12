from starlette.testclient import TestClient
from app.asgi_gateway import asgi

def test_health_ok():
    c = TestClient(asgi)
    r = c.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json().get("ok") is True
