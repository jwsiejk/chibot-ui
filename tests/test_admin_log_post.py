from app.asgi_gateway import app as flask_app
from app.api_v1 import admin as admin_mod


def _decode(resp):
    return resp.data.decode("utf-8", errors="ignore")


def _install_log_iter(monkeypatch):
    def _iter(live: bool = False):  # pragma: no cover - simple drain helper
        while admin_mod._LOG_Q:
            yield admin_mod._LOG_Q.popleft()

    monkeypatch.setattr(admin_mod, "admin_log_iter", _iter, raising=False)


def test_admin_log_post_appends_event(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")
    monkeypatch.setenv("ENABLE_ADMIN_SSE", "1")

    admin_mod._LOG_Q.clear()
    admin_mod._STEP = 0
    _install_log_iter(monkeypatch)

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


def test_admin_logs_sse_default_heartbeat(monkeypatch):
    monkeypatch.delenv("ENABLE_ADMIN_SSE", raising=False)
    monkeypatch.delenv("ADMIN_SSE_E2E_KEY", raising=False)

    admin_mod._LOG_Q.clear()
    admin_mod._STEP = 0

    def _iter(live: bool = False):
        while admin_mod._LOG_Q:
            yield admin_mod._LOG_Q.popleft()

    monkeypatch.setattr(admin_mod, "admin_log_iter", _iter, raising=False)

    client = flask_app.test_client()
    resp = client.get("/api/v1/admin/logs", buffered=True)

    assert resp.status_code == 200
    text = _decode(resp)
    assert "\"event\": \"heartbeat\"" in text


def test_admin_log_post_accepts_sse_token(monkeypatch):
    monkeypatch.setenv("ENABLE_ADMIN_SSE", "1")
    monkeypatch.setenv("ADMIN_SSE_E2E_KEY", "secret-token")

    admin_mod._LOG_Q.clear()
    admin_mod._STEP = 0
    _install_log_iter(monkeypatch)

    client = flask_app.test_client()

    csrf_resp = client.get("/api/v1/csrf")
    token = csrf_resp.get_json()["csrf"]

    payload = {
        "token": "secret-token",
        "kind": "media",
        "label": "mic_start",
        "details": {"source": "mic"},
    }

    rv = client.post(
        "/api/v1/admin/log",
        json=payload,
        headers={"X-CSRF-Token": token},
    )
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["ok"] is True
    assert data["kind"] == "media"

    payload_asr = {
        "token": "secret-token",
        "kind": "asr:start",
        "label": "asr_start",
        "session_id": "sid-42",
    }

    rv2 = client.post(
        "/api/v1/admin/log",
        json=payload_asr,
        headers={"X-CSRF-Token": token},
    )
    assert rv2.status_code == 200
    data2 = rv2.get_json()
    assert data2["ok"] is True
    assert data2["kind"] == "asr:start"

    stream = client.get("/api/v1/admin/logs?k=secret-token", buffered=True)
    text = _decode(stream)

    assert "\"kind\": \"media\"" in text
    assert "\"kind\": \"asr:start\"" in text
    assert "\"source\": \"mic\"" in text
    assert "\"token\"" not in text


def test_admin_log_helper_mirrors_to_admin_sse(monkeypatch):
    monkeypatch.setenv("ENABLE_ADMIN_SSE", "1")
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")

    admin_mod._LOG_Q.clear()
    admin_mod._STEP = 0

    client = flask_app.test_client()
    with client.session_transaction() as sess:
        sess["user"] = {"email": "admin@example.com"}

    from app.logging import admin_log as admin_log_helper

    admin_log_helper("Hello world", email="prod@example.com", role="system")

    stream = client.get("/api/v1/admin/logs", buffered=True)
    text = _decode(stream)

    assert "\"kind\": \"admin_log\"" in text
    assert "\"message\": \"Hello world\"" in text
    assert "\"email\": \"prod@example.com\"" in text
