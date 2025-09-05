
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

