from app.flow.trace import FlowStore


def fresh_store() -> FlowStore:
    FlowStore._instance = None  # type: ignore[attr-defined]
    return FlowStore()


def emit_fault(store: FlowStore, session_id: str, fault: str, turn_id: str) -> None:
    store.emit(
        session_id=session_id,
        level="flow",
        phase="turn",
        type_="asr_path_fault",
        who="system",
        meta={
            "turn_id": turn_id,
            "fault": fault,
            "src": "server_asr",
            "missing_source": False,
        },
    )


def test_asr_fault_counts_aggregated_per_session() -> None:
    store = fresh_store()
    emit_fault(store, "sid", "no_partials", "1")
    emit_fault(store, "sid", "vendor_close", "1")
    emit_fault(store, "sid", "no_partials", "2")

    snapshot = store.list("sid")
    assert snapshot["asr_faults_session"] == {"no_partials": 2, "vendor_close": 1}

    sessions = store.sessions()
    assert sessions[0]["asr_faults_session"]["no_partials"] == 2
    assert sessions[0]["asr_faults_session"]["vendor_close"] == 1


def test_fault_counts_isolated_between_sessions() -> None:
    store = fresh_store()
    emit_fault(store, "sid_a", "no_partials", "1")
    emit_fault(store, "sid_b", "vendor_close", "1")

    snapshot_a = store.list("sid_a")
    snapshot_b = store.list("sid_b")

    assert snapshot_a["asr_faults_session"] == {"no_partials": 1}
    assert snapshot_b["asr_faults_session"] == {"vendor_close": 1}

    summaries = {entry["session_id"]: entry for entry in store.sessions()}
    assert summaries["sid_a"]["asr_faults_session"] == {"no_partials": 1}
    assert summaries["sid_b"]["asr_faults_session"] == {"vendor_close": 1}
