import gzip
import hashlib
import io
import json
import zipfile

import pytest

from app.api_v1 import flow as flow_api
from app.asgi_gateway import app as flask_app
from app.flow.trace import FlowStore
from app.admin_log import clear_admin_log_history_for_tests, emit as admin_log_emit


@pytest.fixture(autouse=True)
def reset_flow_store():
    store = FlowStore()
    store._init()
    flow_api._CLIENT_BREADCRUMB_HITS.clear()
    clear_admin_log_history_for_tests()
    yield
    store._init()
    flow_api._CLIENT_BREADCRUMB_HITS.clear()
    clear_admin_log_history_for_tests()


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


def _csrf_headers(client):
    resp = client.get("/api/v1/csrf")
    token = resp.headers.get("X-CSRF-Token") or (resp.get_json() or {}).get("csrf")
    return {"X-CSRF-Token": token}


def test_flow_breadcrumb_records_client_event():
    client = flask_app.test_client()
    headers = _csrf_headers(client)
    payload = {
        "session_id": "sess-client",
        "event": "ws_error",
        "meta": {"code": 4400},
        "ts_ms": 123456,
    }

    resp = client.post("/api/v1/flow/breadcrumb", json=payload, headers=headers)
    assert resp.status_code == 204

    store = FlowStore()
    events = store.list("sess-client", levels=("debug",)).get("events", [])
    assert events
    event = next(evt for evt in events if evt.get("type") == "client_ws_error")
    meta = event.get("meta") or {}
    assert meta.get("code") == 4400
    assert meta.get("ts_ms") == 123456


def test_flow_breadcrumb_requires_session_and_event():
    client = flask_app.test_client()
    headers = _csrf_headers(client)

    resp = client.post("/api/v1/flow/breadcrumb", json={"event": "oops"}, headers=headers)
    assert resp.status_code == 400

    resp = client.post(
        "/api/v1/flow/breadcrumb",
        json={"session_id": "sess", "meta": {}},
        headers=headers,
    )
    assert resp.status_code == 400


def test_flow_breadcrumb_rate_limit(monkeypatch):
    client = flask_app.test_client()
    headers = _csrf_headers(client)
    clock = {"now": 1000.0}

    def fake_monotonic():
        return clock["now"]

    monkeypatch.setattr(flow_api.time, "monotonic", fake_monotonic)

    session_id = "sess-rate"
    payload = {"session_id": session_id, "event": "vad_gate_open"}

    for _ in range(flow_api._CLIENT_BREADCRUMB_LIMIT):
        resp = client.post("/api/v1/flow/breadcrumb", json=payload, headers=headers)
        assert resp.status_code == 204

    resp = client.post("/api/v1/flow/breadcrumb", json=payload, headers=headers)
    assert resp.status_code == 429


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
    assert resp.headers["X-Flow-Mode"] == "redacted"
    assert resp.headers["X-Flow-Payload-Bytes"] == str(len(resp.data))
    assert resp.headers["X-Flow-Payload-Sha1"] == hashlib.sha1(resp.data).hexdigest()

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
        assert meta_payload["mode"] == "redacted"


def test_flow_handoff_full_mode_includes_manifest(admin_env):
    store = FlowStore()
    store.emit(
        "sess-full",
        "flow",
        "session",
        "session_open",
        "system",
        meta={"text": "Full transcript event"},
    )
    store.emit(
        "sess-full",
        "flow",
        "session",
        "session_config",
        "system",
        meta={"config": {"foo": "bar", "nested": {"value": 1}}},
    )
    store.emit(
        "sess-full",
        "debug",
        "session",
        "payload_sig",
        "system",
        meta={"path": "dg.message", "sha1_8": "feedbeef", "payload": "keep"},
    )

    client = flask_app.test_client()
    csrf_resp = client.get("/api/v1/csrf", headers=admin_env)
    token = csrf_resp.headers.get("X-CSRF-Token")
    headers = dict(admin_env)
    if token:
        headers["X-CSRF-Token"] = token

    body = {
        "session_id": "sess-full",
        "levels": ["flow", "debug"],
        "prompt": "Investigate deeply",
        "options": {
            "mode": "full",
            "include": {"ws": True, "logs": False},
            "privacy": {"pii_scrub": True, "redaction": "minimal"},
            "limits": {"max_bytes": 5_000_000},
        },
    }

    resp = client.post("/api/v1/flow/handoff", json=body, headers=headers)
    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("application/zip")
    assert resp.headers["X-Flow-Redacted"] == "0"
    assert resp.headers["X-Flow-Mode"] == "full"
    assert resp.headers["X-Flow-Payload-Bytes"] == str(len(resp.data))
    assert resp.headers["X-Flow-Payload-Sha1"] == hashlib.sha1(resp.data).hexdigest()

    with zipfile.ZipFile(io.BytesIO(resp.data)) as archive:
        names = set(archive.namelist())
        assert {
            "prompt.txt",
            "manifest.json",
            "events/flow.ndjson.gz",
            "config/config.json",
        } <= names

        prompt_text = archive.read("prompt.txt").decode("utf-8").strip()
        assert prompt_text == "Investigate deeply"

        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        assert manifest["schema_version"] == "1.0"
        assert manifest["session_id"] == "sess-full"
        assert manifest["meta"]["mode"] == "full"
        assert manifest["meta"]["redacted"] is False
        assert manifest["meta"]["include"]["ws"] is False
        assert "logs" not in manifest["meta"]["include"]
        assert manifest["meta"]["privacy"]["pii_scrub"] is True
        assert manifest["meta"]["privacy"]["redaction"] == "minimal"
        assert manifest["meta"]["limits"]["max_bytes"] == 5_000_000
        assert manifest["event_count"] >= 2

        files_meta = {entry["path"]: entry for entry in manifest["files"]}
        for path, entry in files_meta.items():
            data = archive.read(path)
            assert entry["bytes"] == len(data)
            assert entry["sha1"] == hashlib.sha1(data).hexdigest()
            assert entry["sha1_first8"] == entry["sha1"][:8]

        events_text = gzip.decompress(archive.read("events/flow.ndjson.gz")).decode("utf-8")
        assert "Full transcript event" in events_text
        assert "keep" in events_text

        config_payload = json.loads(
            archive.read("config/config.json").decode("utf-8")
        )
        assert config_payload["foo"] == "bar"
        assert config_payload["nested"]["value"] == 1


def test_flow_handoff_includes_optional_artifacts(admin_env):
    store = FlowStore()
    session_id = "sess-optional"

    store.emit(session_id, "flow", "session", "session_open", "system")
    store.emit(
        session_id,
        "debug",
        "session",
        "ws_frame_in",
        "system",
        meta={"type": "text", "bytes": 12, "route": "client"},
    )
    store.emit(
        session_id,
        "debug",
        "session",
        "ws_frame_out",
        "system",
        meta={"type": "binary", "bytes": 18, "route": "bus"},
    )
    store.emit(
        session_id,
        "debug",
        "client",
        "client_ws_error",
        "client",
        meta={"code": 4400},
    )

    admin_log_emit("ws_conn_open", sid=session_id, session_id=session_id, message="open")

    client = flask_app.test_client()
    csrf_resp = client.get("/api/v1/csrf", headers=admin_env)
    token = csrf_resp.headers.get("X-CSRF-Token")
    headers = dict(admin_env)
    if token:
        headers["X-CSRF-Token"] = token

    body = {
        "session_id": session_id,
        "levels": ["flow", "debug"],
        "prompt": "Diag",
        "mode": "full",
        "options": {
            "mode": "full",
            "include": {"ws": True, "logs": True},
            "privacy": {"pii_scrub": False, "redaction": "minimal"},
        },
    }

    resp = client.post("/api/v1/flow/handoff", json=body, headers=headers)
    assert resp.status_code == 200

    with zipfile.ZipFile(io.BytesIO(resp.data)) as archive:
        names = set(archive.namelist())
        assert "ws/frames.ndjson.gz" in names
        assert "client/console.log.gz" in names
        assert "server/server.log.gz" in names

        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        include_meta = manifest.get("meta", {}).get("include", {})
        assert include_meta.get("ws") is True
        assert include_meta.get("logs") is True

        frames_payload = gzip.decompress(archive.read("ws/frames.ndjson.gz")).decode("utf-8").strip()
        frame_entries = [json.loads(line) for line in frames_payload.split("\n") if line.strip()]
        assert any(frame.get("direction") == "in" for frame in frame_entries)
        assert any(frame.get("direction") == "out" for frame in frame_entries)

        client_payload = gzip.decompress(archive.read("client/console.log.gz")).decode("utf-8").strip()
        client_entries = [json.loads(line) for line in client_payload.split("\n") if line.strip()]
        assert any(entry.get("type") == "client_ws_error" for entry in client_entries)

        server_payload = gzip.decompress(archive.read("server/server.log.gz")).decode("utf-8").strip()
        server_entries = [json.loads(line) for line in server_payload.split("\n") if line.strip()]
        assert any(entry.get("event") == "ws_conn_open" for entry in server_entries)


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
