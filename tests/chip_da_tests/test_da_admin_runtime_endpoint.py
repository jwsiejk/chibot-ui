# test_da_admin_runtime_endpoint.py
import os, sys, pathlib, json
ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from app import create_app

def test_admin_runtime_endpoint_ok(monkeypatch):
    # Allow mocks in this test env so selection helpers don't raise
    monkeypatch.setenv("ALLOW_MOCK_PROVIDERS","true")
    monkeypatch.delenv("APP_ENV", raising=False)
    app = create_app()
    with app.test_client() as c:
        r = c.get("/api/v1/admin/runtime")
        assert r.status_code == 200
        j = r.get_json()
        assert j and j.get("ok") is True
        rt = j.get("runtime") or {}
        assert "providers" in rt and "keys" in rt and "versions" in rt