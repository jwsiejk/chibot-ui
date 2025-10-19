import gzip
import io
import json
import zipfile

import pytest

from app.asgi_gateway import app as flask_app
from app.flow import FlowStore
from app.admin_log import admin_log_emit, clear_admin_log_history_for_tests


@pytest.fixture(autouse=True)
def _reset_flow_store():
    store = FlowStore()
    store._init()
    clear_admin_log_history_for_tests()
    yield
    store._init()
    clear_admin_log_history_for_tests()


@pytest.fixture
def admin_env(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")
    return {"X-User-Email": "admin@example.com"}


def _decode_gzip_bytes(data: bytes) -> str:
    return gzip.decompress(data).decode("utf-8")


def test_flow_handoff_pii_scrub_redacted(admin_env):
    store = FlowStore()
    session_id = "sess-pii-redacted"
    store.emit(
        session_id,
        "flow",
        "session",
        "session_open",
        "client",
        meta={"notes": "Email hi@example.com token sk-secretVALUE123 and ip 203.0.113.25"},
    )

    client = flask_app.test_client()
    csrf_resp = client.get("/api/v1/csrf", headers=admin_env)
    token = csrf_resp.headers.get("X-CSRF-Token")
    headers = dict(admin_env)
    if token:
        headers["X-CSRF-Token"] = token

    body = {
        "session_id": session_id,
        "prompt": "Investigate",
        "options": {"privacy": {"pii_scrub": True}},
    }

    resp = client.post("/api/v1/flow/handoff", json=body, headers=headers)
    assert resp.status_code == 200
    assert resp.headers.get("X-Flow-PII-Scrubbed") == "1"

    with zipfile.ZipFile(io.BytesIO(resp.data)) as archive:
        ndjson = archive.read("flow.ndjson").decode("utf-8")
        assert "hi@example.com" not in ndjson
        assert "sk-secretVALUE123" not in ndjson
        assert "203.0.113.25" not in ndjson
        assert "[email:" in ndjson
        assert "[token:" in ndjson
        meta_payload = json.loads(archive.read("meta.json").decode("utf-8"))
        assert meta_payload["privacy"]["pii_scrub"] is True


def test_flow_handoff_pii_scrub_full_includes_logs(admin_env):
    store = FlowStore()
    session_id = "sess-pii-full"
    store.emit(
        session_id,
        "flow",
        "session",
        "session_config",
        "system",
        meta={"config": {"agent": "alpha"}},
    )
    store.emit(
        session_id,
        "flow",
        "turn",
        "assistant_speak",
        "assistant",
        meta={"notes": "Token sk-newtokenVALUE999"},
    )
    store.emit(
        session_id,
        "debug",
        "client",
        "client_console_error",
        "client",
        meta={"details": "External IP 198.51.100.44"},
    )

    admin_log_emit(
        {
            "event": "diag_latency",
            "session_id": session_id,
            "message": "diag External IP 198.51.100.22",
        }
    )

    client = flask_app.test_client()
    csrf_resp = client.get("/api/v1/csrf", headers=admin_env)
    token = csrf_resp.headers.get("X-CSRF-Token")
    headers = dict(admin_env)
    if token:
        headers["X-CSRF-Token"] = token

    body = {
        "session_id": session_id,
        "options": {
            "mode": "full",
            "include": {"logs": True},
            "privacy": {"pii_scrub": True},
        },
    }

    resp = client.post("/api/v1/flow/handoff", json=body, headers=headers)
    assert resp.status_code == 200
    assert resp.headers.get("X-Flow-PII-Scrubbed") == "1"

    with zipfile.ZipFile(io.BytesIO(resp.data)) as archive:
        events_payload = archive.read("events/flow.ndjson.gz")
        events_text = _decode_gzip_bytes(events_payload)
        assert "sk-newtokenVALUE999" not in events_text
        assert "198.51.100.44" not in events_text
        assert "[token:" in events_text

        client_log = archive.read("client/console.log.gz")
        client_text = _decode_gzip_bytes(client_log)
        assert "198.51.100.44" not in client_text
        assert "[ip:" in client_text

        server_log = archive.read("server/server.log.gz")
        server_text = _decode_gzip_bytes(server_log)
        assert "198.51.100.22" not in server_text
        assert "[ip:" in server_text


def test_flow_handoff_respects_max_bytes(admin_env):
    store = FlowStore()
    session_id = "sess-max"
    store.emit(session_id, "flow", "session", "session_open", "system")

    client = flask_app.test_client()
    csrf_resp = client.get("/api/v1/csrf", headers=admin_env)
    token = csrf_resp.headers.get("X-CSRF-Token")
    headers = dict(admin_env)
    if token:
        headers["X-CSRF-Token"] = token

    body = {
        "session_id": session_id,
        "options": {"mode": "full", "limits": {"max_bytes": 10}},
    }

    resp = client.post("/api/v1/flow/handoff", json=body, headers=headers)
    assert resp.status_code == 413
    payload = resp.get_json()
    assert payload == {"error": "export_too_large"}
