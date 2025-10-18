import pytest

from app.flow.trace import (
    CONFIRM_CLOSE_TYPE,
    CONFIRM_OPEN_TYPE,
    FlowStore,
)


@pytest.fixture
def flow_env(monkeypatch):
    store = FlowStore()
    store._init()

    clock = {"now": 1000.0}

    def fake_monotonic():
        return clock["now"]

    monkeypatch.setattr("app.flow.trace.time.monotonic", fake_monotonic)

    def advance(ms: int) -> None:
        clock["now"] += ms / 1000.0

    yield store, advance, clock

    store._init()


def test_emit_and_list_basic(flow_env):
    store, advance, _ = flow_env
    session_id = "sess-basic"

    first_id = store.emit(session_id, "flow", "session", "session_open", "client")
    assert first_id
    advance(10)
    second_id = store.emit(session_id, "transition", "session", "asr_ready", "system")
    assert second_id and second_id != first_id

    data = store.list(session_id, expand="all")
    events = data["events"]
    assert [evt["id"] for evt in events] == [first_id, second_id]
    assert data["next_since_ms"] > events[-1]["t_rel_ms"]


def test_emit_dedupe_window(flow_env):
    store, advance, _ = flow_env
    session_id = "sess-dedupe"

    eid1 = store.emit(session_id, "flow", "session", "session_open", "system")
    advance(50)  # 50 ms < dedupe window
    eid2 = store.emit(session_id, "flow", "session", "session_open", "system")
    assert eid1 == eid2
    advance(200)  # outside dedupe window
    eid3 = store.emit(session_id, "flow", "session", "session_open", "system")
    assert eid3 != eid1


def test_add_batch_and_expand(flow_env):
    store, advance, _ = flow_env
    session_id = "sess-batch"

    parent_id = store.emit(session_id, "flow", "turn", "confirm_open", "assistant")
    advance(5)
    child_id = store.emit(
        session_id,
        "transition",
        "turn",
        "asr_partial_first",
        "client",
        parent_id=parent_id,
    )

    added = store.add_batch(session_id, parent_id, "transcript", ["hello", "world"])
    assert added is True

    snapshot = store.list(session_id, expand="flow")
    events = snapshot["events"]
    assert events[0]["id"] == parent_id
    assert events[0]["batches"][0]["kind"] == "transcript"
    assert events[0]["batches"][0]["items"] == ["hello", "world"]
    assert events[0]["children"][0]["id"] == child_id


def test_injected_confirm_close(flow_env):
    store, advance, _ = flow_env
    session_id = "sess-inject"

    store.emit(session_id, "flow", "confirm", CONFIRM_OPEN_TYPE, "assistant", meta={"confirm_id": "1"})
    advance(4500)
    data = store.list(session_id)
    types = [evt["type"] for evt in data["events"]]
    assert CONFIRM_CLOSE_TYPE in types
    warning_meta = next(evt["meta"] for evt in data["events"] if evt["type"] == CONFIRM_CLOSE_TYPE)
    assert warning_meta.get("__warning") == "inferred_close"


def test_levels_and_since_filter(flow_env):
    store, advance, _ = flow_env
    session_id = "sess-filters"

    store.emit(session_id, "flow", "session", "session_open", "system")
    advance(10)
    store.emit(session_id, "transition", "session", "asr_ready", "system")

    flow_only = store.list(session_id, levels=["flow"])
    assert len(flow_only["events"]) == 1

    since_data = store.list(session_id, since_ms=5)
    ids = [evt["type"] for evt in since_data["events"]]
    assert "asr_ready" in ids
    assert "session_open" not in ids
