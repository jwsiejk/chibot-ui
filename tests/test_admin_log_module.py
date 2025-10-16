from app import admin_log


def setup_function():
    admin_log.clear_admin_log_history_for_tests()


def test_admin_log_emit_normalises_payload():
    evt = admin_log.admin_log_emit({"kind": "diag", "sid": "  session-1  ", "text": "hello"})
    assert evt["step"] == 1
    assert evt["session_id"] == "session-1"
    assert evt["text"] == "hello"


def test_get_admin_log_history_filters_by_after_and_limit():
    for idx in range(4):
        admin_log.admin_log_emit({"kind": f"evt-{idx}"})

    assert len(admin_log.get_admin_log_history()) == 4
    filtered = admin_log.get_admin_log_history(after_step=2)
    assert [evt["kind"] for evt in filtered] == ["evt-2", "evt-3"]
    limited = admin_log.get_admin_log_history(limit=1)
    assert len(limited) == 1 and limited[0]["kind"] == "evt-3"
