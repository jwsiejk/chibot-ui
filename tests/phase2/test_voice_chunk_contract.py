import base64
from app import create_app

def _csrf_headers(client):
    r = client.get("/api/v1/csrf")
    return {"X-CSRF-Token": r.headers.get("X-CSRF-Token")}

def test_voice_chunk_contract_enqueue(monkeypatch):
    app = create_app()
    client = app.test_client()

    view = client.application.view_functions["api_v1.voice.stt_stub"]

    class DummyProvider:
        def __init__(self):
            self.calls = []

        def synth(self, text):
            self.calls.append(text)
            return b"voice-bytes", [{"t": 0.0, "v": "ah"}]

    provider = DummyProvider()

    monkeypatch.setitem(view.__globals__, "get_tts_provider", lambda cfg: provider)

    emitted = []
    bus_obj = view.__globals__["bus"]

    def fake_broadcast(session_id, frame):
        emitted.append((session_id, frame))

    monkeypatch.setattr(bus_obj, "broadcast", fake_broadcast)

    def fake_schedule_frames(session_id, frames, **kwargs):
        for fr in frames:
            bus_obj.broadcast(session_id, fr)

    monkeypatch.setitem(view.__globals__, "schedule_frames", fake_schedule_frames)

    sid = "s-voice2"
    headers = dict(_csrf_headers(client), **{"X-Forwarded-For": "4.4.4.4"})

    r = client.post(
        "/api/v1/voice/stt",
        json={"session_id": sid},
        headers=headers,
    )
    assert r.status_code == 200, r.data
    j = r.get_json()
    assert j["ok"] is True
    assert j["is_final"] is True
    assert j.get("transcript", "") == ""
    frames = [fr for _, fr in emitted]
    assert any(fr.get("type") == "text" for fr in frames)
    audio_frames = [fr for fr in frames if fr.get("type") == "audio_chunk"]
    assert audio_frames, "expected audio chunk frame to be scheduled"
    encoded = audio_frames[0].get("audio_b64") or audio_frames[0].get("base64")
    assert encoded
    assert base64.b64decode(encoded.encode("ascii")) == b"voice-bytes"
    assert emitted, "expected frames broadcast to bus"
    assert all(evt[0] == sid for evt in emitted)
