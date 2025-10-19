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


@pytest.fixture
def flow_clock(monkeypatch):
    clock = {"now": 1000.0}

    def fake_monotonic():
        return clock["now"]

    monkeypatch.setattr("app.flow.trace.time.monotonic", fake_monotonic)

    def advance(ms: int) -> None:
        clock["now"] += ms / 1000.0

    return advance


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


def test_flow_trace_happy_path_integration(admin_env, flow_clock):
    store = FlowStore()
    session_id = "sess-happy"

    store.emit(session_id, "flow", "session", "session_open", "system")
    flow_clock(50)
    confirm_id = store.emit(
        session_id,
        "flow",
        "turn",
        "confirm_open",
        "assistant",
        meta={"turn_id": "1"},
    )
    flow_clock(200)
    store.emit(
        session_id,
        "transition",
        "turn",
        "asr_partial_first",
        "client",
        meta={"turn_id": "1"},
        parent_id=confirm_id,
    )
    flow_clock(150)
    store.emit(
        session_id,
        "flow",
        "turn",
        "confirm_close",
        "assistant",
        meta={"turn_id": "1", "reason": "commit"},
        parent_id=confirm_id,
    )

    client = flask_app.test_client()
    resp = client.get(
        "/api/v1/flow/trace",
        query_string={"session_id": session_id, "expand": "flow", "levels": "flow,transition"},
        headers=admin_env,
    )
    assert resp.status_code == 200
    payload = resp.get_json()
    types = [evt["type"] for evt in payload["events"]]
    assert types[:3] == ["session_open", "confirm_open", "asr_partial_first"]
    assert payload["hints"] == []


def test_flow_trace_barge_in_integration(admin_env, flow_clock):
    store = FlowStore()
    session_id = "sess-barge"

    first = store.emit(session_id, "transition", "turn", "barge_in", "client")
    assert first
    store.emit(session_id, "transition", "turn", "barge_pause", "system")
    flow_clock(500)
    suppressed = store.emit(session_id, "transition", "turn", "barge_in", "client")
    assert suppressed is None
    store.emit(session_id, "transition", "turn", "barge_resume", "system")
    flow_clock(50)
    second = store.emit(session_id, "transition", "turn", "barge_in", "client")
    assert second and second != first

    client = flask_app.test_client()
    resp = client.get(
        "/api/v1/flow/trace",
        query_string={"session_id": session_id, "levels": "transition", "expand": "all"},
        headers=admin_env,
    )
    assert resp.status_code == 200
    data = resp.get_json()
    barge_types = [evt["type"] for evt in data["events"] if evt["type"].startswith("barge")]
    assert barge_types.count("barge_in") == 2


def test_flow_trace_silence_timeout_integration(admin_env, flow_clock):
    store = FlowStore()
    session_id = "sess-silence"

    store.emit(
        session_id,
        "flow",
        "confirm",
        "confirm_open",
        "assistant",
        meta={"confirm_id": "silence"},
    )
    flow_clock(4500)

    client = flask_app.test_client()
    resp = client.get(
        "/api/v1/flow/trace",
        query_string={"session_id": session_id, "expand": "flow"},
        headers=admin_env,
    )
    assert resp.status_code == 200
    events = resp.get_json()["events"]
    inferred = [evt for evt in events if evt["type"] == "confirm_close"]
    assert inferred
    assert inferred[-1]["meta"].get("__warning") == "inferred_close"


def test_flow_trace_manual_only_integration(admin_env):
    store = FlowStore()
    session_id = "sess-manual"

    event_id = store.emit(
        session_id,
        "debug",
        "session",
        "runtime_flags",
        "system",
        meta={"manual_mode_manual_only": True},
    )
    assert event_id

    client = flask_app.test_client()
    resp = client.get(
        "/api/v1/flow/trace",
        query_string={"session_id": session_id, "levels": "debug", "expand": "none"},
        headers=admin_env,
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["events"][0]["meta"]["manual_mode_manual_only"] is True


def test_flow_trace_asr_recover_hint(admin_env):
    store = FlowStore()
    session_id = "sess-asr"

    err_id = store.emit(session_id, "transition", "asr", "asr_error", "system")
    assert err_id
    recover_id = store.emit(session_id, "transition", "asr", "recover_ok", "system")
    assert recover_id

    client = flask_app.test_client()
    resp = client.get(
        "/api/v1/flow/trace",
        query_string={"session_id": session_id, "levels": "transition", "expand": "all"},
        headers=admin_env,
    )
    assert resp.status_code == 200
    hints = resp.get_json()["hints"]
    assert any(hint["id"].startswith("asr_recovered:") for hint in hints)


def test_flow_trace_slow_tts_hint(admin_env):
    store = FlowStore()
    session_id = "sess-tts"

    end_id = store.emit(session_id, "flow", "tts", "tts_end", "assistant")
    assert end_id
    store.emit(
        session_id,
        "debug",
        "tts",
        "tts_metrics",
        "assistant",
        meta={"first_byte_ms": 800},
        parent_id=end_id,
    )

    client = flask_app.test_client()
    resp = client.get(
        "/api/v1/flow/trace",
        query_string={"session_id": session_id, "levels": "flow,debug", "expand": "all"},
        headers=admin_env,
    )
    assert resp.status_code == 200
    hints = resp.get_json()["hints"]
    assert any(hint["id"].startswith("tts_slow:") for hint in hints)


def test_flow_trace_large_raw_batches(admin_env):
    store = FlowStore()
    session_id = "sess-raw"

    raw_id = store.emit(session_id, "raw", "audio", "mic_raw_chunk", "system")
    assert raw_id
    frames = [f"frame-{idx}" for idx in range(300)]
    assert store.add_batch(session_id, raw_id, "raw_audio", frames) is True

    client = flask_app.test_client()
    resp = client.get(
        "/api/v1/flow/trace",
        query_string={"session_id": session_id, "levels": "raw", "expand": "all"},
        headers=admin_env,
    )
    assert resp.status_code == 200
    data = resp.get_json()
    event = data["events"][0]
    assert event["type"] == "mic_raw_chunk"
    batch_items = event["batches"][0]["items"]
    assert batch_items[0] == "frame-0" and batch_items[-1] == "frame-299"
