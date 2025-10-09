
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

    def _csrf_headers():
        resp = client.get("/api/v1/csrf")
        token = resp.headers.get("X-CSRF-Token")
        return {"X-CSRF-Token": token}

    from app.middleware import rate_limit as rate_limit_mw
    monkeypatch.setattr(rate_limit_mw, "_MAX", 2)
    monkeypatch.setattr(rate_limit_mw, "_MAX_VOICE", 2)
    monkeypatch.setattr(rate_limit_mw, "_WINDOW", 10.0)
    rate_limit_mw._BUCKETS.clear()

    from app.db import db
    db.memory.setdefault("rl_buckets", {}).clear()

    # Chat: trigger limiter with rapid calls (limit ~2 in 10s window)
    headers = _csrf_headers()
    headers.update({"X-Forwarded-For": "3.3.3.3"})
    chat_statuses = []
    for i in range(5):
        hv = dict(headers, **{"Idempotency-Key": f"msg-{i}"})
        rv = client.post("/api/v1/chat", json={"session_id": "s-chat", "text": "hello"}, headers=hv)
        chat_statuses.append(rv.status_code)
        if rv.status_code == 429:
            break
    assert 429 in chat_statuses, f"expected chat rate limiting, got {chat_statuses}"

    # Voice STT: trigger limiter with 3 rapid calls (limit=2 in 10s window)
    headers = dict(_csrf_headers(), **{"X-Forwarded-For": "1.2.3.5"})
    data = {"session_id":"s1","mime":"audio/webm"}
    stt_statuses = []
    for i in range(5):
        rv = client.post("/api/v1/voice/stt", headers=headers,
                         data={**data, "file": (io.BytesIO(b"FAKEAUDIO"), "audio.webm")})
        stt_statuses.append(rv.status_code)
        if rv.status_code == 429:
            break
        assert rv.status_code in (200, 429), rv.data
    assert 429 in stt_statuses, f"expected voice STT rate limiting, got {stt_statuses}"
def test_one_ws_per_tab_guard_module():
    # Guard logic should exist and work
    guard = importlib.import_module("app.ws.one_tab")
    key = "sessionX:tabY"
    guard.release(key)
    assert guard.acquire(key) is True
    assert guard.acquire(key) is False, "second acquire for same key must fail"
    guard.release(key)
    assert guard.acquire(key) is True, "after release, acquire should succeed"

def test_email_transcript_on_end(monkeypatch):
    from app.db import db
    before = len(db.list_emails())
    app = import_app()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user"] = {"email": "test@example.com"}
    view = client.application.view_functions["api_v1.chat.post_chat"]

    def fake_send_transcript(*args, **kwargs):
        db.add_email("test@example.com", "Transcript", "Body")
        return True

    monkeypatch.setitem(view.__globals__, "send_transcript", fake_send_transcript)
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

