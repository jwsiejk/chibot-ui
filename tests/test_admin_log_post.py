from app.asgi_gateway import app as flask_app


def _decode(resp):
    return resp.data.decode("utf-8", errors="ignore")


def test_admin_log_post_appends_event(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")

    client = flask_app.test_client()
    with client.session_transaction() as sess:
        sess["user"] = {"email": "admin@example.com"}

    csrf_resp = client.get("/api/v1/csrf")
    token = csrf_resp.get_json()["csrf"]

    payload = {"kind": "admin_diag", "label": "diagnostic_start", "session_id": "sid-123"}
    rv = client.post(
        "/api/v1/admin/log",
        json=payload,
        headers={"X-CSRF-Token": token},
    )
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["ok"] is True
    assert data["kind"] == "admin_diag"
    assert data["label"] == "diagnostic_start"

    stream = client.get("/api/v1/admin/logs")
    text = _decode(stream)
    assert "\"kind\": \"admin_diag\"" in text
    assert "\"session_id\": \"sid-123\"" in text
