from typing import Any, Dict

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


def test_emit_includes_normalized_source(flow_env):
    store, _, _ = flow_env
    session_id = "sess-src"

    store.emit(
        session_id,
        "flow",
        "session",
        "explicit_src",
        "system",
        meta={"src": "ingress_gateway"},
    )
    store.emit(
        session_id,
        "flow",
        "session",
        "component_src",
        "system",
        meta={"component": "tts_engine"},
    )
    store.emit(
        session_id,
        "flow",
        "session",
        "default_server",
        "system",
    )
    store.emit(
        session_id,
        "flow",
        "session",
        "default_client",
        "client",
    )
    store.emit(
        session_id,
        "flow",
        "session",
        "unknown_src",
        "assistant",
    )
    store.emit(
        session_id,
        "flow",
        "session",
        "detail_src",
        "client",
        meta={"detail": {"src": "client_audio"}},
    )

    events = {evt["type"]: evt for evt in store.list(session_id, expand="all")["events"]}

    assert events["explicit_src"]["src"] == "ingress_gateway"
    assert "missing_source" not in events["explicit_src"]
    assert events["component_src"]["src"] == "tts_engine"
    assert "missing_source" not in events["component_src"]
    assert events["default_server"]["src"] == "server_core"
    assert "missing_source" not in events["default_server"]
    assert events["default_client"]["src"] == "client_ui"
    assert "missing_source" not in events["default_client"]
    assert events["unknown_src"]["src"] == "unknown"
    assert events["unknown_src"]["missing_source"] is True
    assert events["unknown_src"]["who"] == "assistant"
    assert events["detail_src"]["src"] == "client_audio"
    assert "missing_source" not in events["detail_src"]


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


def _list_with_hints(store: FlowStore, session_id: str) -> Dict[str, Any]:
    return store.list(
        session_id,
        expand="all",
        levels=("flow", "transition", "debug"),
    )


def test_hint_no_asr_after_ready(flow_env):
    store, advance, _ = flow_env
    session_id = "sess-hint-asr"

    confirm_id = store.emit(
        session_id,
        "flow",
        "turn",
        CONFIRM_OPEN_TYPE,
        "system",
        meta={"turn_id": "1"},
    )
    assert confirm_id
    advance(1300)
    store.emit(
        session_id,
        "flow",
        "turn",
        CONFIRM_CLOSE_TYPE,
        "system",
        meta={"reason": "timeout"},
    )

    payload = _list_with_hints(store, session_id)
    hint_ids = {hint["id"] for hint in payload["hints"]}
    assert any(hid.startswith("no_asr_after_ready:") for hid in hint_ids)


def test_hint_evidence_never_met(flow_env):
    store, advance, _ = flow_env
    session_id = "sess-hint-evidence"

    confirm_id = store.emit(
        session_id,
        "flow",
        "turn",
        CONFIRM_OPEN_TYPE,
        "system",
        meta={"turn_id": "2"},
    )
    assert confirm_id
    advance(50)
    vad_id = store.emit(
        session_id,
        "transition",
        "mic",
        "vad_gate_open",
        "system",
        meta={"reason": "speech"},
    )
    assert vad_id
    advance(400)
    store.emit(
        session_id,
        "flow",
        "turn",
        CONFIRM_CLOSE_TYPE,
        "system",
        meta={"reason": "abort"},
    )

    payload = _list_with_hints(store, session_id)
    matches = [hint for hint in payload["hints"] if hint["id"].startswith("evidence_never_met:")]
    assert matches
    anchors = matches[0]["anchors"]
    assert confirm_id in anchors and vad_id in anchors


def test_hint_commit_blocked_min_tokens(flow_env):
    store, advance, _ = flow_env
    session_id = "sess-hint-gate"

    confirm_id = store.emit(
        session_id,
        "flow",
        "turn",
        CONFIRM_OPEN_TYPE,
        "system",
        meta={"turn_id": "3"},
    )
    assert confirm_id
    advance(100)
    gate_id = store.emit(
        session_id,
        "debug",
        "turn",
        "gate_check",
        "system",
        meta={"rule": "min_tokens", "value": 1, "threshold": 4, "passed": False},
        parent_id=confirm_id,
    )
    assert gate_id

    payload = _list_with_hints(store, session_id)
    matches = [hint for hint in payload["hints"] if hint["id"].startswith("commit_blocked_min_tokens:")]
    assert matches
    assert confirm_id in matches[0]["anchors"]


def test_hint_tts_slow_and_post_hold(flow_env):
    store, advance, _ = flow_env
    session_id = "sess-hint-tts"

    tts_start = store.emit(
        session_id,
        "flow",
        "turn",
        TTS_START_TYPE,
        "assistant",
        meta={"turn_id": "4"},
    )
    assert tts_start
    advance(200)
    tts_end = store.emit(
        session_id,
        "flow",
        "turn",
        TTS_END_TYPE,
        "assistant",
        meta={"turn_id": "4"},
    )
    assert tts_end
    store.emit(
        session_id,
        "debug",
        "turn",
        "tts_metrics",
        "assistant",
        meta={"first_byte_ms": 800},
        parent_id=tts_end,
    )
    advance(300)
    vad_id = store.emit(
        session_id,
        "transition",
        "mic",
        "vad_gate_open",
        "system",
        meta={"reason": "tts_mask"},
    )
    assert vad_id

    payload = _list_with_hints(store, session_id)
    hint_ids = {hint["id"] for hint in payload["hints"]}
    assert any(hid.startswith("tts_slow:") for hid in hint_ids)
    hold_hints = [hint for hint in payload["hints"] if hint["id"].startswith("post_tts_hold_overlap:")]
    assert hold_hints
    assert tts_end in hold_hints[0]["anchors"]


def test_hint_asr_recovered_and_queue(flow_env):
    store, advance, _ = flow_env
    session_id = "sess-hint-misc"

    err_id = store.emit(
        session_id,
        "transition",
        "asr",
        "asr_error",
        "system",
        meta={"code": "ws"},
    )
    assert err_id
    advance(200)
    ok_id = store.emit(
        session_id,
        "transition",
        "asr",
        "recover_ok",
        "system",
        meta={"path": "asr"},
    )
    assert ok_id
    store.emit(
        session_id,
        "debug",
        "mic",
        "queue_depth",
        "system",
        meta={"name": "mic", "depth": 12, "watermark": 10},
    )

    payload = _list_with_hints(store, session_id)
    ids = {hint["id"] for hint in payload["hints"]}
    assert any(hid.startswith("asr_recovered:") for hid in ids)
    assert any(hid.startswith("queue_pressure:") for hid in ids)


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


def test_add_batch_for_event_lookup(flow_env):
    store, advance, _ = flow_env
    session_id = "sess-batch-event"

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
    assert child_id

    added = store.add_batch_for_event(parent_id, "transcript", ["hi", "there"])
    assert added is True
    missing = store.add_batch_for_event("missing", "transcript", ["nope"])
    assert missing is False

    snapshot = store.list(session_id, expand="flow")
    event = snapshot["events"][0]
    assert event["batches"][0]["items"] == ["hi", "there"]
    assert event["children"][0]["id"] == child_id


def test_list_expand_modes(flow_env):
    store, advance, _ = flow_env
    session_id = "sess-expand"

    parent_id = store.emit(session_id, "flow", "turn", "confirm_open", "assistant")
    child_id = store.emit(
        session_id,
        "transition",
        "turn",
        "asr_partial_first",
        "client",
        parent_id=parent_id,
    )
    grandchild_id = store.emit(
        session_id,
        "debug",
        "turn",
        "latency_tick",
        "system",
        parent_id=child_id,
    )
    assert grandchild_id

    none_payload = store.list(session_id, expand="none")
    assert none_payload["events"][0]["children"] == [child_id]

    flow_payload = store.list(session_id, expand="flow")
    assert flow_payload["events"][0]["children"][0]["id"] == child_id
    assert flow_payload["events"][0]["children"][0]["children"] == [grandchild_id]

    ids_payload = store.list(session_id, expand=f"ids:{child_id}")
    child_entry = next(evt for evt in ids_payload["events"] if evt["id"] == child_id)
    assert child_entry["children"][0]["id"] == grandchild_id


def test_get_returns_nested_event(flow_env):
    store, _, _ = flow_env
    session_id = "sess-get"

    parent_id = store.emit(session_id, "flow", "turn", "confirm_open", "assistant")
    child_id = store.emit(
        session_id,
        "transition",
        "turn",
        "asr_partial_first",
        "client",
        parent_id=parent_id,
    )
    assert child_id

    payload = store.get(session_id, parent_id)
    assert payload
    assert payload["id"] == parent_id
    assert payload["children"][0]["id"] == child_id
