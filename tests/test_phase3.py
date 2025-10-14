
import os, time, importlib, sys, pathlib, io
REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

os.environ["USE_MOCK_VENDORS"] = "1"

def import_app():
    app_mod = importlib.import_module("app.asgi_gateway")
    flask_app = getattr(app_mod, "app", None) or getattr(app_mod, "flask_app", None)
    assert flask_app is not None, "Flask app not exposed"
    return flask_app

def test_barge_state_commit_after_confirm(monkeypatch):
    barge = importlib.import_module("app.ws.barge")
    called = {"commit":0, "states":[]}
    def send_state(phase):
        called["states"].append(phase)
    def on_commit():
        called["commit"] += 1
    state = barge.BargeState()
    ok = state.start(confirm_ms=50, on_commit=on_commit, send_state=send_state)  # 50ms confirm
    assert ok is True
    # Wait a bit longer than confirm_ms
    time.sleep(0.12)
    assert called["commit"] == 1, "commit should be called after confirm_ms elapses"
    assert "paused" in called["states"]

def test_barge_state_cancel_before_confirm():
    barge = importlib.import_module("app.ws.barge")
    called = {"commit":0, "states":[]}
    def send_state(phase):
        called["states"].append(phase)
    def on_commit():
        called["commit"] += 1
    state = barge.BargeState()
    ok = state.start(confirm_ms=200, on_commit=on_commit, send_state=send_state)
    assert ok is True
    # Cancel quickly before 200ms
    time.sleep(0.05)
    state.cancel(send_state=send_state)
    # Give some time to ensure commit would have fired otherwise
    time.sleep(0.25)
    assert called["commit"] == 0, "commit should NOT be called if canceled"
    assert "assistant_speaking" in called["states"]

def test_email_transcript_on_end():
    from app.db import db
    before = len(db.list_emails())
    app = import_app()
    client = app.test_client()
    rv = client.post("/api/v1/chat", json={"cmd":"end_session", "session_id":"s-email-3"})
    assert rv.status_code == 200, rv.data
    data = rv.get_json()
    assert data and data.get("emailed") is True
    after = len(db.list_emails())
    assert after == before + 1

def test_nudges_arm_and_cancel_via_chat():
    # Verify that a nudge can be armed and then canceled by user input
    nudges = importlib.import_module("app.policy.nudges")
    from app.db import db
    sid = "s-nudge-3"
    db.update_config({"nudges_enabled": True, "nudge_delay_ms": 100})  # speed up for test
    nudges._cancel_all()
    ok = nudges.arm_nudge(sid)
    assert ok is True and sid in nudges._scheduled
    # Now cancel via chat
    app = import_app()
    client = app.test_client()
    rv = client.post("/api/v1/chat", json={"session_id": sid, "text": "hello"})
    assert rv.status_code == 200
    time.sleep(0.05)
    assert sid not in nudges._scheduled, "nudge should be canceled by chat input"

def test_one_ws_per_tab_guard_still_present():
    guard = importlib.import_module("app.ws.one_tab")
    key = "s3:tabA"
    assert guard.acquire(key) is True
    assert guard.acquire(key) is False
    guard.release(key)
    assert guard.acquire(key) is True


def test_confirm_window_commits_after_valid_partial():
    from app.ws.confirm_window import ConfirmWindow
    import struct

    win = ConfirmWindow(
        min_duration_ms=400,
        max_duration_ms=900,
        max_gap_ms=400,
        snr_threshold_db=4.0,
    )
    start = 0.0
    win.start(start)
    loud_chunk = struct.pack("<80h", *([1200] * 80))

    assert win.observe_chunk(loud_chunk, start + 0.12).action is None
    assert win.observe_partial(3, 0.7, start + 0.28).action is None
    decision = win.observe_chunk(loud_chunk, start + 0.45)
    assert decision.action == "commit"
    metrics = decision.metrics or {}
    assert metrics.get("reason") == "chunk"
    assert metrics.get("partial_tokens") == 3


def test_confirm_window_aborts_on_low_confidence_partial():
    from app.ws.confirm_window import ConfirmWindow
    import struct

    win = ConfirmWindow(min_duration_ms=350, max_duration_ms=900, snr_threshold_db=4.0)
    start = 0.0
    win.start(start)
    chunk = struct.pack("<80h", *([900] * 80))

    win.observe_chunk(chunk, start + 0.1)
    decision = win.observe_partial(3, 0.3, start + 0.2)
    assert decision.action == "abort"
    metrics = decision.metrics or {}
    assert metrics.get("reason") == "partial_low_confidence"


def test_confirm_window_tolerates_initial_gap_then_aborts():
    from app.ws.confirm_window import ConfirmWindow
    import struct

    win = ConfirmWindow(
        min_duration_ms=400,
        max_duration_ms=900,
        max_gap_ms=180,
        snr_threshold_db=6.0,
    )
    start = 0.0
    win.start(start)
    loud_chunk = struct.pack("<80h", *([1200] * 80))

    assert win.observe_chunk(loud_chunk, start + 0.1).action is None

    # Gap of 500ms should be tolerated once for jitter
    assert win.observe_chunk(loud_chunk, start + 0.6).action is None

    # A second large gap should abort the confirmation window
    decision = win.observe_chunk(loud_chunk, start + 1.2)
    assert decision.action == "abort"
    metrics = decision.metrics or {}
    assert metrics.get("reason") == "gap"
    assert metrics.get("gap_grace_used") is True


def test_confirm_window_borderline_snr_commits_with_slack():
    from app.ws.confirm_window import ConfirmWindow
    import struct

    win = ConfirmWindow(
        min_duration_ms=200,
        max_duration_ms=800,
        max_gap_ms=300,
        min_tokens=2,
        snr_threshold_db=8.0,
        snr_slack_db=0.5,
    )
    start = 0.0
    win.start(start)
    chunk = struct.pack("<80h", *([1000] * 80))

    # prime SNR tracking
    win.observe_chunk(chunk, start + 0.1)
    win.observe_chunk(chunk, start + 0.25)
    # Force borderline SNR slightly below threshold
    win.snr_db = win.snr_threshold_db - 0.3

    decision = win.observe_partial(2, 0.8, start + 0.5)
    assert decision.action == "commit"
    metrics = decision.metrics or {}
    assert metrics.get("reason") == "partial"

