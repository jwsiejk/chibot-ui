import io
import json
import zipfile

import pytest

from app.asgi_gateway import app as flask_app
from app.flow.trace import FlowStore


@pytest.fixture(autouse=True)
def reset_flow_store():
    store = FlowStore()
    store._init()
    yield
    store._init()


@pytest.fixture
def admin_env(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")
    return {"X-User-Email": "admin@example.com"}


def test_flow_trace_endpoint(admin_env):
    store = FlowStore()
    event_id = store.emit("sess-api", "flow", "session", "session_open", "system")
    assert event_id

    client = flask_app.test_client()
    resp = client.get("/api/v1/flow/trace", query_string={"session_id": "sess-api"}, headers=admin_env)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["session_id"] == "sess-api"
    assert any(evt["id"] == event_id for evt in data["events"])


def test_flow_event_endpoint(admin_env):
    store = FlowStore()
    event_id = store.emit("sess-event", "flow", "session", "session_open", "system")

    client = flask_app.test_client()
    resp = client.get(
        "/api/v1/flow/event",
        query_string={"session_id": "sess-event", "id": event_id},
        headers=admin_env,
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["id"] == event_id


def test_flow_export_ndjson(admin_env):
    store = FlowStore()
    store.emit("sess-export", "flow", "session", "session_open", "system")
    store.emit("sess-export", "transition", "session", "asr_ready", "system")

    client = flask_app.test_client()
    resp = client.get(
        "/api/v1/flow/export.ndjson",
        query_string={"session_id": "sess-export"},
        headers=admin_env,
    )
    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("application/x-ndjson")
    body = resp.data.decode("utf-8").strip().split("\n")
    assert len(body) >= 2


def test_flow_export_redaction_masks_sensitive_fields(admin_env):
    store = FlowStore()
    store.emit(
        "sess-redact",
        "flow",
        "session",
        "session_open",
        "system",
        meta={"text": "Hello from user"},
    )
    store.emit(
        "sess-redact",
        "debug",
        "session",
        "payload_sig",
        "system",
        meta={"bytes": 42, "sha1_8": "abcdef12", "path": "llm.prompt", "body": "LLM prompt"},
    )
    store.emit(
        "sess-redact",
        "debug",
        "session",
        "device_tag",
        "system",
        meta={"device_label": "Jane's MacBook Pro"},
    )

    client = flask_app.test_client()
    resp = client.get(
        "/api/v1/flow/export.ndjson",
        query_string={"session_id": "sess-redact", "levels": "flow,debug"},
        headers=admin_env,
    )
    assert resp.status_code == 200
    lines = [line for line in resp.data.decode("utf-8").split("\n") if line.strip()]
    assert lines
    events = [json.loads(line) for line in lines]

    text_meta = next(evt.get("meta", {}) for evt in events if evt.get("meta", {}).get("text"))
    assert "Hello from user" not in text_meta.get("text", "")
    assert "[redacted" in text_meta.get("text", "")

    payload_meta = next(evt.get("meta", {}) for evt in events if evt.get("type") == "payload_sig")
    assert "body" not in payload_meta
    assert payload_meta.get("bytes") == 42
    assert payload_meta.get("sha1_8") == "abcdef12"
    assert payload_meta.get("category") == "llm"

    device_meta = next(evt.get("meta", {}) for evt in events if evt.get("meta", {}).get("device_label"))
    assert device_meta.get("device_label") in {"mac", "macbook"}


def test_flow_sessions_endpoint(admin_env):
    store = FlowStore()
    store.emit("sess-a", "flow", "session", "session_open", "system")
    store.emit("sess-b", "flow", "session", "session_open", "system")
    store.emit("sess-b", "transition", "turn", "turn_start", "system")

    client = flask_app.test_client()
    resp = client.get("/api/v1/flow/sessions", headers=admin_env)
    assert resp.status_code == 200
    data = resp.get_json()
    assert any(item["session_id"] == "sess-a" for item in data["sessions"])
    assert any(item["session_id"] == "sess-b" for item in data["sessions"])

    resp_filtered = client.get(
        "/api/v1/flow/sessions",
        query_string={"q": "sess-b", "limit": "1"},
        headers=admin_env,
    )
    assert resp_filtered.status_code == 200
    filtered = resp_filtered.get_json()
    assert len(filtered["sessions"]) == 1
    assert filtered["sessions"][0]["session_id"] == "sess-b"


def test_flow_admin_guard():
    client = flask_app.test_client()
    resp = client.get("/api/v1/flow/trace", query_string={"session_id": "nope"})
    assert resp.status_code == 403


def test_flow_handoff_returns_redacted_zip(admin_env):
    store = FlowStore()
    store.emit(
        "sess-hand",
        "flow",
        "session",
        "session_open",
        "system",
        meta={"text": "Hand-off transcript"},
    )
    store.emit(
        "sess-hand",
        "debug",
        "session",
        "payload_sig",
        "system",
        meta={"bytes": 99, "sha1_8": "12345678", "path": "dg.message", "payload": "should hide"},
    )

    client = flask_app.test_client()
    csrf_resp = client.get("/api/v1/csrf", headers=admin_env)
    token = csrf_resp.headers.get("X-CSRF-Token")
    handoff_headers = dict(admin_env)
    if token:
        handoff_headers["X-CSRF-Token"] = token
    resp = client.post(
        "/api/v1/flow/handoff",
        json={"session_id": "sess-hand", "levels": ["flow", "debug"], "prompt": "Investigate"},
        headers=handoff_headers,
    )
    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("application/zip")
    assert resp.headers["X-Flow-Redacted"] == "1"

    buffer = io.BytesIO(resp.data)
    with zipfile.ZipFile(buffer) as archive:
        names = set(archive.namelist())
        assert {"flow.ndjson", "prompt.txt", "meta.json"}.issubset(names)
        ndjson_text = archive.read("flow.ndjson").decode("utf-8")
        assert "Hand-off transcript" not in ndjson_text
        prompt_text = archive.read("prompt.txt").decode("utf-8").strip()
        assert prompt_text == "Investigate"
        meta_payload = json.loads(archive.read("meta.json").decode("utf-8"))
        assert meta_payload["payload_sig"]["sha1_8"]
        assert meta_payload["payload_sig"]["bytes"] >= 0
