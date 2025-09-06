
import os, importlib, sys, pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# Force sqlite for the test so DAL connects without network
os.environ["DATABASE_URL"] = "sqlite:////mnt/data/ask_chip_health.sqlite"
os.environ["USE_MOCK_VENDORS"] = "1"

def import_app():
    app_mod = importlib.import_module("app.asgi_gateway")
    return getattr(app_mod, "app", None) or getattr(app_mod, "flask_app", None)

def test_db_health_endpoint():
    app = import_app()
    c = app.test_client()
    r = c.get("/api/v1/admin/db/health")
    assert r.status_code == 200, r.data
    j = r.get_json()
    assert j["ok"] is True
    assert j["connected"] is True
    assert j["dialect"] in ("sqlite", "postgresql", "memory")

def test_admin_ui_badge_present():
    pages = list((REPO / "app" / "templates").glob("admin.html"))
    assert pages, "admin.html expected"
    html = pages[0].read_text(encoding="utf-8")
    assert 'id="dbBadge"' in html
    assert "/api/v1/admin/db/health" in html
