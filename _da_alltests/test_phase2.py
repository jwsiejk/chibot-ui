
import os, io, time, importlib, sys, pathlib, json
from contextlib import contextmanager

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

os.environ["USE_MOCK_VENDORS"] = "1"   # ensure mocks
os.environ["RATE_LIMIT_WINDOW_S"] = "10"
os.environ["RATE_LIMIT_MAX"] = "2"

def import_app():
    # Import the Flask app from asgi_gateway
    app_mod = importlib.import_module("app.asgi_gateway")
    flask_app = getattr(app_mod, "app", None) or getattr(app_mod, "flask_app", None)
    assert flask_app is not None, "Flask app not exposed"
    return flask_app


def test_rate_limit_chat_and_voice_stt(monkeypatch):
    app = import_app()
    client = app.test_client()

    # Chat: ensure guard is wired (blueprint before_request is present)
    chat_mod = importlib.import_module("app.api_v1.chat")
    assert hasattr(chat_mod, "_chat_rl_guard"), "chat rate-limit guard missing"

    # Voice STT: trigger limiter with 3 rapid calls (limit=2 in 10s window)
    data = {"session_id":"s1","mime":"audio/webm"}
    for i in range(2):
        rv = client.post("/api/v1/voice/stt", headers={"X-Forwarded-For":"1.2.3.5"},
                         data={**data, "file": (io.BytesIO(b"FAKEAUDIO"), "audio.webm")},
                         content_type="multipart/form-data")
        assert rv.status_code == 200, rv.data
    rv = client.post("/api/v1/voice/stt", headers={"X-Forwarded-For":"1.2.3.5"},
                     data={**data, "file": (io.BytesIO(b"FAKEAUDIO"), "audio.webm")},
                     content_type="multipart/form-data")
    assert rv.status_code == 429, f"expected 429 on stt overflow, got {rv.status_code}"
def test_one_ws_per_tab_guard_module():
    # Guard logic should exist and work
    guard = importlib.import_module("app.ws.one_tab")
    key = "sessionX:tabY"
    assert guard.acquire(key) is True
    assert guard.acquire(key) is False, "second acquire for same key must fail"
    guard.release(key)
    assert guard.acquire(key) is True, "after release, acquire should succeed"

def test_email_transcript_on_end():
    from app.db import db
    before = len(db.list_emails())
    app = import_app()
    client = app.test_client()
    rv = client.post("/api/v1/chat", json={"cmd":"end_session", "session_id":"s-email"}, headers={"X-Forwarded-For":"9.9.9.9"})
    assert rv.status_code == 200, rv.data
    data = rv.get_json()
    assert data and data.get("emailed") is True
    after = len(db.list_emails())
    assert after == before + 1, "transcript email should be recorded (mock)"

def test_nudges_policy_armed_and_canceled(monkeypatch):
    # Ensure the policy module exists and can arm/cancel
    nudges = importlib.import_module("app.policy.nudges")
    from app.db import db

    # reset state
    nudges._cancel_all()
    sid = "s-nudge"
    cfg = db.update_config({"nudges_enabled": True, "nudge_delay_ms": 4200, "nudge_backoff_after_ignored": 2})

    # Arm a nudge
    nudges.arm_nudge(sid)
    assert sid in nudges._scheduled, "nudge should be scheduled"

    # Cancel on new user input
    nudges.cancel_nudge(sid)
    assert sid not in nudges._scheduled, "nudge should be canceled after user input"

    # Integrate with chat route: calling /api/v1/chat with text should cancel any pending nudge
    nudges.arm_nudge(sid)
    app = import_app()
    client = app.test_client()
    rv = client.post("/api/v1/chat", json={"session_id": sid, "text": "hello"}, headers={"X-Forwarded-For":"7.7.7.7"})
    assert rv.status_code == 200
    assert sid not in nudges._scheduled, "chat user text should cancel a pending nudge"

