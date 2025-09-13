
def _csrf_headers(client):
    h = {}
    r = client.get("/api/v1/csrf")
    tok = r.headers.get("X-CSRF-Token")
    if tok:
        h["X-CSRF-Token"] = tok
    return h


from app import create_app

def test_chat_requires_idempotency_key():
    app = create_app()
    client = app.test_client()
    r = client.post("/api/v1/chat", json={"text":"hello","session_id":"s2"}, headers=_csrf_headers(client))
    assert r.status_code == 400
    j = r.get_json()
    assert j.get("error") == "missing_idempotency_key"

def test_chat_idempotency_header_same_turn_id():
    app = create_app()
    client = app.test_client()
    headers = {"Idempotency-Key":"msg-001"}
    r1 = client.post("/api/v1/chat", json={"text":"hello","session_id":"s2"}, headers={**headers, **_csrf_headers(client)})
    j1 = r1.get_json()
    assert "turn_id" in j1 and j1.get("user_msg_id") == "msg-001"
    # Duplicate request with same header
    r2 = client.post("/api/v1/chat", json={"text":"hello again","session_id":"s2"}, headers={**headers, **_csrf_headers(client)})
    j2 = r2.get_json()
    assert j2.get("idempotent") is True
    assert j2.get("turn_id") == j1.get("turn_id")
