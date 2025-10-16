
import os, json, time
import pytest

# Ensure admin + secret for session
os.environ.setdefault("ADMIN_EMAILS", "jwsiejk@purestorage.com")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("CI_FAST", "1")

def asgi_app():
    from app.asgi_gateway import asgi
    return asgi

def get_csrf(client):
    # CSRF route provided by middleware.make_csrf_route(app)
    r = client.get("/api/v1/auth/csrf")
    assert r.status_code == 200
    j = r.json()
    return j.get("csrf") or j.get("token")

def test_greet_ws_chat_flow():
    from starlette.testclient import TestClient
    app = asgi_app()
    with TestClient(app) as client:
        # 1) WS connect
        sid = "test-session"
        with client.websocket_connect(f"/ws/v1/chat?session_id={sid}&tab=tab") as ws:
            # 2) Greet (GET)
            r = client.get(f"/api/v1/greet?session_id={sid}")
            assert r.status_code in (200, 304)

            # 3) Chat POST with CSRF
            tok = get_csrf(client)
            r = client.post("/api/v1/chat", json={"text":"hello", "session_id": sid}, headers={"X-CSRF-Token": tok})
            assert r.status_code == 200
            j = r.json()
            assert j.get("ok") is True
            assert j.get("turn_id")

            # 4) Receive at least one assistant frame
            got_chunk = False
            t0 = time.time()
            while time.time() - t0 < 3.0:
                m = ws.receive_text()
                if not m: continue
                try:
                    obj = json.loads(m)
                except Exception:
                    continue
                if obj.get("type") in ("assistant_chunk","assistant_end","text","end"):
                    got_chunk = True
                    break
            assert got_chunk, "Expected assistant frames over WS"

def test_admin_logs_ui_and_auth():
    from starlette.testclient import TestClient
    app = asgi_app()
    with TestClient(app) as client:
        # logs-ui requires admin; use header fallback
        ui = client.get("/api/v1/admin/logs-ui", headers={"X-User-Email":"jwsiejk@purestorage.com"})
        assert ui.status_code == 200
        # Log snapshot requires the same admin guard
        snap = client.get("/api/v1/admin/logs", headers={"X-User-Email":"jwsiejk@purestorage.com"})
        assert snap.status_code == 200
        body = snap.json()
        assert body["ok"] is True
