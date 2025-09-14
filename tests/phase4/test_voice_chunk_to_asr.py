
import base64, time
from app import create_app
from app.ws.bus import bus

def _csrf_headers(client):
    r = client.get("/api/v1/csrf")
    tok = r.headers.get("X-CSRF-Token") or r.headers.get("x-csrf-token")
    return {"X-CSRF-Token": tok} if tok else {}

def test_voice_chunk_to_asr_partials_and_final():
    app = create_app()
    client = app.test_client()
    sid = "p4-asr"
    q = bus.subscribe(sid)

    blob = base64.b64encode(b"abc").decode("ascii")
    for i in range(1, 7):
        r = client.post(f"/api/v1/voice/chunk?session_id={sid}", json={
            "user_msg_id": "u-msg-4",
            "chunk_seq": i,
            "audio_b64": blob,
            "format": "webm-opus"
        }, headers=_csrf_headers(client))
        assert r.status_code == 200

    saw_partial = 0
    saw_final = False
    deadline = time.time() + 2.5
    while time.time() < deadline and (saw_partial < 2 or not saw_final):
        try:
            fr = q.get(timeout=0.2)
        except Exception:
            continue
        t = (fr.get("type") or "").lower()
        if t == "user_partial":
            saw_partial += 1
        elif t == "user_final":
            saw_final = True
            break

    assert saw_partial >= 1, "Expected at least one ASR partial"
    assert saw_final, "Expected ASR final after chunks"
