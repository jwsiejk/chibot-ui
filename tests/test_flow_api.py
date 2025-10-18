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
