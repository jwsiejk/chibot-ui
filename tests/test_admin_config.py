
import json
from starlette.testclient import TestClient
from app.asgi_gateway import asgi

def test_admin_roundtrip_and_validation():
    client = TestClient(asgi)
    # Read defaults
    r = client.get("/api/v1/admin/config")
    assert r.status_code == 200
    cfg = r.json()
    assert cfg["stt_mode"] in ("batch","stream")
    # Validation fail: bad listen_url
    bad = {
        "stt_mode": "stream",
        "deepgram": {**cfg["deepgram"], "listen_url": "http://bad"}
    }
    r = client.post("/api/v1/admin/config", json=bad)
    assert r.status_code == 400
    # Fix and save
    good = {
        "stt_mode": "stream",
        "deepgram": {**cfg["deepgram"], "listen_url": "wss://example.ok/v1/listen"}
    }
    r = client.post("/api/v1/admin/config", json=good)
    assert r.status_code == 200
    new_cfg = r.json()["config"]
    assert new_cfg["stt_mode"] == "stream"
    assert new_cfg["deepgram"]["listen_url"].startswith("wss://")
