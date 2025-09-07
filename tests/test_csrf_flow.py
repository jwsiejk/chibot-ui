import importlib

def _app():
    app_mod = importlib.import_module("app")
    app = app_mod.create_app()
    app.config["TESTING"] = True
    return app

def test_csrf_endpoint_sets_header():
    app = _app()
    c = app.test_client()
    r = c.get("/api/v1/auth/csrf")
    assert r.status_code == 200
    assert "X-CSRF-Token" in r.headers

def test_post_with_token_succeeds_on_admin_route():
    app = _app()
    c = app.test_client()
    r = c.get("/api/v1/auth/csrf")
    token = r.headers.get("X-CSRF-Token")
    resp = c.post("/api/v1/admin/db/retention/anonymize",
                  json={"email":"test@example.com"},
                  headers={"X-CSRF-Token": token})
    assert resp.status_code in (200, 400)  # 400 if email missing etc., but should NOT be 403
    assert resp.status_code != 403
