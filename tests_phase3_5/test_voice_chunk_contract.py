
import base64
from app import create_app

def _get_csrf(client):
    r = client.get("/api/v1/health")
    token = r.headers.get("X-CSRF-Token") or r.headers.get("X-CSRFToken") or r.headers.get("X-CSRF")
    if not token:
        for c in client.cookie_jar:
            if c.name == "XSRF-TOKEN":
                token = c.value
                break
    assert token, "CSRF token not issued"
    return token

def test_voice_chunk_requires_fields():
    app = create_app()
    c = app.test_client()
    csrf = _get_csrf(c)
    rv = c.post("/api/v1/voice/chunk", json={}, headers={"X-CSRF-Token": csrf})
    assert rv.status_code == 400
    assert rv.json["error"] == "bad_request"

def test_voice_chunk_accepts_minimal(monkeypatch):
    app = create_app()
    c = app.test_client()
    csrf = _get_csrf(c)
    # monkeypatch the get_manager used by voice module directly
    calls = {}
    class DummyMgr:
        def enqueue(self, sid, item):
            calls["sid"] = sid
            calls["item"] = item
    import app.api_v1.voice as voice_mod
    monkeypatch.setattr(voice_mod, "get_manager", lambda: DummyMgr(), raising=True)
    audio = base64.b64encode(b"fakewebm").decode("ascii")
    rv = c.post("/api/v1/voice/chunk", json={"sid":"S1","audio_b64":audio,"chunk_seq":1,"user_msg_id":"U1"}, headers={"X-CSRF-Token": csrf})
    assert rv.status_code == 200
    assert rv.json["ok"] is True
    assert calls["sid"] == "S1"
    assert calls["item"]["user_msg_id"] == "U1"
    assert calls["item"]["chunk_seq"] == 1
    assert isinstance(calls["item"]["data"], (bytes, bytearray))
