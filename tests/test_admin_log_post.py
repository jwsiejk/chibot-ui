from app.asgi_gateway import app as flask_app
from app.admin_log import clear_admin_log_history_for_tests


def _with_admin_session(client, email: str = "admin@example.com"):
    with client.session_transaction() as sess:
        sess["user"] = {"email": email}


def _csrf(client):
    resp = client.get("/api/v1/csrf")
    return resp.get_json()["csrf"]


def setup_function():
    clear_admin_log_history_for_tests()


def test_admin_log_post_appends_event(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")

    client = flask_app.test_client()
    _with_admin_session(client)

    token = _csrf(client)
    payload = {"kind": "admin_diag", "label": "diagnostic_start", "session_id": "sid-123"}

    resp = client.post("/api/v1/admin/log", json=payload, headers={"X-CSRF-Token": token})
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True

    logs = client.get("/api/v1/admin/logs").get_json()
    assert logs["ok"] is True
    assert logs["latest_step"] == 1
    assert logs["events"][0]["session_id"] == "sid-123"


def test_admin_logs_support_limit_and_after(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")

    client = flask_app.test_client()
    _with_admin_session(client)
    token = _csrf(client)

    for idx in range(5):
        resp = client.post(
            "/api/v1/admin/log",
            json={"kind": f"diag-{idx}", "label": "step", "session_id": "sid"},
            headers={"X-CSRF-Token": token},
        )
        assert resp.status_code == 200

    all_logs = client.get("/api/v1/admin/logs").get_json()
    assert len(all_logs["events"]) == 5

    last_step = all_logs["events"][-1]["step"]
    recent = client.get(f"/api/v1/admin/logs?limit=2&after={last_step-3}").get_json()
    assert len(recent["events"]) == 2
    assert recent["events"][0]["kind"] == "diag-3"
    assert recent["events"][1]["kind"] == "diag-4"


def test_admin_logs_allow_e2e_token(monkeypatch):
    monkeypatch.setenv("ADMIN_SSE_E2E_KEY", "secret-token")

    client = flask_app.test_client()
    token = _csrf(client)

    resp = client.post(
        "/api/v1/admin/log",
        json={"token": "secret-token", "kind": "media", "label": "mic_start"},
        headers={"X-CSRF-Token": token},
    )
    assert resp.status_code == 200

    logs = client.get("/api/v1/admin/logs?k=secret-token").get_json()
    assert logs["events"][0]["kind"] == "media"


def test_admin_log_helper_records_event(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")

    client = flask_app.test_client()
    _with_admin_session(client)

    from app.logging import admin_log as admin_log_helper

    admin_log_helper("Hello world", email="prod@example.com", role="system")

    logs = client.get("/api/v1/admin/logs").get_json()
    assert logs["events"][0]["kind"] == "admin_log"
    assert logs["events"][0]["message"] == "Hello world"
    assert logs["events"][0]["email"] == "prod@example.com"
