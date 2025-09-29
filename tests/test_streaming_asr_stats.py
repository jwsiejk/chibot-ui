from app.asgi_gateway import app as flask_app
from app.services.streaming_asr import stream_manager as sm


def _csrf(client):
    return client.get("/api/v1/auth/csrf").get_json()["csrf"]


def _login(client, email):
    token = _csrf(client)
    client.post(
        "/api/v1/auth/login",
        json={"email": email},
        headers={"X-CSRF-Token": token},
    )


def test_streaming_status_reports_provider_errors(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")

    mgr = sm._CompatManager()
    monkeypatch.setattr(sm, "_COMPAT_SINGLETON", mgr, raising=True)

    def _fake_submit_bg(coro, timeout=None):
        try:
            coro.close()
        except Exception:
            pass
        return None

    monkeypatch.setattr(sm, "_submit_bg", _fake_submit_bg, raising=True)

    client = flask_app.test_client()
    _login(client, "admin@example.com")

    sid = "sess-123"
    mgr._emit("asr", "provider_open", session_id=sid)
    mgr._emit("asr", "asr_open", session_id=sid)
    mgr._emit("asr", "asr_partial", session_id=sid)
    mgr._emit("asr", "asr_partial", session_id=sid)
    mgr._emit("asr", "asr_final", session_id=sid)
    mgr._emit("asr", "asr_error", session_id=sid, error="boom")

    stats = mgr.stats(sid)
    assert stats["partials"] == 2
    assert stats["finals"] == 1
    assert stats["provider_errors"] == 1
    assert stats["err_count"] == 1
    assert stats["err"] == "boom"
    assert stats["active"] is True

    mgr.end(sid, wait_for_final=False)

    stats_after_end = mgr.stats(sid)
    assert stats_after_end["active"] is False
    assert stats_after_end["last_event"] == "end"

    resp = client.get("/api/v1/admin/diagnostics/streaming_status")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["partials"] == 2
    assert payload["finals"] == 1
    assert payload["provider_errors"] == 1
    assert payload["sessions"][sid]["provider_errors"] == 1
    assert payload["sessions"][sid]["err"] == "boom"
    assert payload["sessions"][sid]["active"] is False

    resp_single = client.get(f"/api/v1/admin/diagnostics/streaming_status?sid={sid}")
    assert resp_single.status_code == 200
    payload_single = resp_single.get_json()
    assert payload_single["provider_errors"] == 1
    assert payload_single["err"] == "boom"
