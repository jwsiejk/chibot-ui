import pytest

from app.flow.trace import (
    ASR_PARTIAL_TYPE,
    BARGE_IN_TYPE,
    BARGE_RESUME_TYPE,
    CONFIRM_CLOSE_TYPE,
    CONFIRM_OPEN_TYPE,
    LLM_FINAL_TYPE,
    LLM_START_TYPE,
    TTS_END_TYPE,
    TTS_START_TYPE,
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


def test_asr_partial_once_per_turn(flow_env):
    store, advance, _ = flow_env
    session_id = "sess-asr"

    meta = {"turn_id": "turn-1"}
    first = store.emit(
        session_id,
        "transition",
        "turn",
        ASR_PARTIAL_TYPE,
        "client",
        meta=meta,
    )
    assert first

    advance(500)
    second = store.emit(
        session_id,
        "transition",
        "turn",
        ASR_PARTIAL_TYPE,
        "client",
        meta=meta,
    )
    assert second == first

    new_meta = {"turn_id": "turn-2"}
    third = store.emit(
        session_id,
        "transition",
        "turn",
        ASR_PARTIAL_TYPE,
        "client",
        meta=new_meta,
    )
    assert third and third != first


def test_barge_in_suppressed_while_paused(flow_env):
    store, advance, _ = flow_env
    session_id = "sess-barge"

    first = store.emit(session_id, "transition", "turn", BARGE_IN_TYPE, "client")
    assert first

    store.emit(session_id, "transition", "turn", "barge_pause", "system")

    advance(2000)
    second = store.emit(session_id, "transition", "turn", BARGE_IN_TYPE, "client")
    assert second is None

    snapshot = store.list(session_id)
    barge_events = [evt for evt in snapshot["events"] if evt["type"] == BARGE_IN_TYPE]
    assert len(barge_events) == 1

    store.emit(session_id, "transition", "turn", BARGE_RESUME_TYPE, "system")
    advance(10)
    third = store.emit(session_id, "transition", "turn", BARGE_IN_TYPE, "client")
    assert third and third != first


def test_tts_start_dedupe_and_forced_close(flow_env):
    store, advance, _ = flow_env
    session_id = "sess-tts"
    meta = {"turn_id": "turn-tts"}

    start = store.emit(
        session_id,
        "flow",
        "turn",
        TTS_START_TYPE,
        "assistant",
        meta=meta,
    )
    assert start

    advance(1000)
    duplicate = store.emit(
        session_id,
        "flow",
        "turn",
        TTS_START_TYPE,
        "assistant",
        meta=meta,
    )
    assert duplicate == start

    advance(121000)
    snapshot = store.list(session_id)
    forced = [evt for evt in snapshot["events"] if evt["type"] == TTS_END_TYPE]
    assert forced
    assert forced[-1]["meta"].get("__warning") == "forced_close"

    restart = store.emit(
        session_id,
        "flow",
        "turn",
        TTS_START_TYPE,
        "assistant",
        meta=meta,
    )
    assert restart and restart != start


def test_llm_forced_close(flow_env):
    store, advance, _ = flow_env
    session_id = "sess-llm"
    meta = {"turn_id": "turn-llm"}

    start = store.emit(
        session_id,
        "flow",
        "turn",
        LLM_START_TYPE,
        "assistant",
        meta=meta,
    )
    assert start

    advance(121000)
    snapshot = store.list(session_id)
    forced = [evt for evt in snapshot["events"] if evt["type"] == LLM_FINAL_TYPE]
    assert forced
    assert forced[-1]["meta"].get("__warning") == "forced_close"

    restart = store.emit(
        session_id,
        "flow",
        "turn",
        LLM_START_TYPE,
        "assistant",
        meta=meta,
    )
    assert restart and restart != start


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
