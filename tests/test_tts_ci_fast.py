
import importlib, os

def test_tts_returns_200_under_ci_fast(monkeypatch):
    monkeypatch.setenv("CI_FAST", "1")
    app_mod = importlib.import_module("app")
    app = app_mod.create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    # fetch csrf
    r = c.get("/api/v1/auth/csrf")
    token = r.headers.get("X-CSRF-Token")
    resp = c.post("/api/v1/voice/tts-with-visemes", json={"text":"hello"}, headers={"X-CSRF-Token": token})
    assert resp.status_code == 200, resp.data
    assert resp.json.get("ok") is True
