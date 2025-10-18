import pytest

from app.flow.emit import add_batch, emit
from app.flow.trace import FlowStore


@pytest.fixture
def flow_env(monkeypatch):
    store = FlowStore.instance()
    store._init()

    clock = {"now": 1000.0}

    def fake_monotonic():
        return clock["now"]

    monkeypatch.setattr("app.flow.trace.time.monotonic", fake_monotonic)

    def advance(ms: int) -> None:
        clock["now"] += ms / 1000.0

    yield store, advance

    store._init()


def test_emit_shim(flow_env):
    store, advance = flow_env
    session_id = "shim-session"

    first_id = emit(session_id, "flow", "session", "session_open", "client")
    assert first_id
    advance(10)
    second_id = emit(session_id, "transition", "session", "asr_ready", "system")
    assert second_id and second_id != first_id

    snapshot = store.list(session_id, expand="all")
    assert [evt["id"] for evt in snapshot["events"]] == [first_id, second_id]


def test_add_batch_shim(flow_env):
    store, _ = flow_env
    session_id = "shim-batch"

    parent_id = emit(session_id, "flow", "turn", "confirm_open", "assistant")
    child_id = emit(
        session_id,
        "transition",
        "turn",
        "asr_partial_first",
        "client",
        parent_id=parent_id,
    )
    assert child_id

    add_batch(parent_id, "transcript", ["hello", "world"])

    snapshot = store.list(session_id, expand="flow")
    events = snapshot["events"]
    assert events[0]["id"] == parent_id
    assert events[0]["batches"][0]["kind"] == "transcript"
    assert events[0]["batches"][0]["items"] == ["hello", "world"]
    assert events[0]["children"][0]["id"] == child_id
