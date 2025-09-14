import base64
from app import create_app

def _csrf_headers(client):
    r = client.get("/api/v1/csrf")
    return {"X-CSRF-Token": r.headers.get("X-CSRF-Token")}

def test_voice_chunk_contract_enqueue(monkeypatch):
    app = create_app()
    client = app.test_client()

    # Patch the voice module's get_manager so we don't require a real provider
    calls = {}
    class DummyMgr:
        def enqueue(self, sid, item):
            calls["sid"] = sid
            calls["item"] = item

    import app.api_v1.voice as voice_mod
    monkeypatch.setattr(voice_mod, "get_manager", lambda: DummyMgr(), raising=True)

    sid = "s-voice2"
    audio = base64.b64encode(b"123").decode("ascii")
    payload = {"sid": sid, "user_msg_id":"u1", "chunk_seq":1, "audio_b64": audio}

    r = client.post("/api/v1/voice/chunk", json=payload, headers=_csrf_headers(client))
    assert r.status_code == 200, r.data
    j = r.get_json()
    assert j["ok"] is True
    assert j["received_seq"] == 1
    assert calls["sid"] == sid
    assert calls["item"]["user_msg_id"] == "u1"
    assert calls["item"]["chunk_seq"] == 1
    assert isinstance(calls["item"]["data"], (bytes, bytearray))
