import pytest

from app.flow import FlowStore
from app.flow.trace import FLOW_DROPPED_TYPE, MAX_BATCH_BYTES


@pytest.fixture(autouse=True)
def _reset_flow_store():
    store = FlowStore()
    store._init()
    yield
    store._init()


def test_logging_backbone_core_events_present():
    store = FlowStore()
    session_id = "sess-core"
    config_payload = {"agent": "alpha", "language": "en"}
    store.emit(
        session_id,
        "flow",
        "session",
        "session_config",
        "system",
        meta={"config": config_payload},
    )
    store.emit(session_id, "flow", "session", "greet_end", "system", meta={"turn_id": "turn-1"})
    store.emit(session_id, "flow", "session", "diag_latency", "system", meta={"latency_ms": 42})

    snapshot = store.snapshot(session_id, expand="all")
    types = [event["type"] for event in snapshot.events]
    assert "session_config" in types
    assert "greet_end" in types
    assert "diag_latency" in types
    assert snapshot.config == config_payload


def test_logging_backbone_emits_flow_dropped_when_capacity_hit(monkeypatch):
    store = FlowStore()
    session_id = "sess-drop"
    monkeypatch.setattr("app.flow.trace.MAX_EVENTS", 3, raising=False)

    for idx in range(5):
        store.emit(
            session_id,
            "flow",
            "session",
            f"event_{idx}",
            "system",
            meta={"seq": idx},
        )

    snapshot = store.snapshot(session_id, expand="all")
    drop_events = [evt for evt in snapshot.events if evt["type"] == FLOW_DROPPED_TYPE]
    assert drop_events, "expected flow_dropped breadcrumb when session truncated"
    last_meta = drop_events[-1]["meta"]
    assert last_meta["reason"] == "event_limit"
    assert last_meta["count"] >= 1


def test_logging_backbone_batch_drop_event():
    store = FlowStore()
    session_id = "sess-batch"
    parent_id = store.emit(session_id, "flow", "session", "session_open", "system")
    assert parent_id

    large_item = "x" * (MAX_BATCH_BYTES // 2)
    with pytest.raises(ValueError):
        store.add_batch(session_id, parent_id, "chunks", [large_item, large_item, large_item])

    snapshot = store.snapshot(session_id, expand="all")
    drop_events = [evt for evt in snapshot.events if evt["type"] == FLOW_DROPPED_TYPE]
    assert drop_events
    assert any(event["meta"].get("reason") == "batch_bytes" for event in drop_events)
