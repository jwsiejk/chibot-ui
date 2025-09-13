
def _csrf_headers(client):
    h = {}
    r = client.get("/api/v1/csrf")
    tok = r.headers.get("X-CSRF-Token")
    if tok:
        h["X-CSRF-Token"] = tok
    return h


import base64
from app import create_app
from app.ws.bus import bus

def test_voice_chunk_contract_and_bus_publish():
    app = create_app()
    client = app.test_client()
    sid = "s-voice2"
    q = bus.subscribe(sid)

    good = base64.b64encode(b"123").decode("ascii")
    r = client.post(f"/api/v1/voice/chunk?session_id={sid}", headers=_csrf_headers(client), json={"user_msg_id":"u1","chunk_seq":1,"audio_b64":good,"format":"webm-opus"})
    assert r.status_code == 200, r.data

    fr = q.get(timeout=1.0)
    assert fr["type"] == "voice_chunk"
    assert fr["session_id"] == sid
    assert fr["user_msg_id"] == "u1"
    assert fr["chunk_seq"] == 1
