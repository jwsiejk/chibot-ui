
import os, io, time, importlib, sys, pathlib, json

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# Use sqlite DATABASE_URL to simulate Neon (no external calls)
os.environ["DATABASE_URL"] = "sqlite:////mnt/data/ask_chip_phase4.sqlite"
os.environ["USE_MOCK_VENDORS"] = "1"

def import_app():
    app_mod = importlib.import_module("app.asgi_gateway")
    return getattr(app_mod, "app", None) or getattr(app_mod, "flask_app", None)

def test_config_persists_with_versioning(monkeypatch):
    # Update config via admin endpoint, then simulate restart and assert persistence and version bump
    app = import_app()
    c = app.test_client()
    rv = c.post("/api/v1/admin/config/update", json={"updates":{"theme":"dark","suggestions_max_items":3}})
    assert rv.status_code == 200, rv.data
    data = rv.get_json()
    assert data["ok"] and data["config"]["theme"] == "dark"
    v1 = data.get("version", 0)

    # Another update
    rv = c.post("/api/v1/admin/config/update", json={"updates":{"theme":"light"}})
    v2 = rv.get_json().get("version", 0)
    assert v2 == v1 + 1, "version should increment"

    # Subscribe and confirm an event is pushed
    conf = importlib.import_module("app.services.config_store")
    q = conf.subscribe()
    conf.update_config({"test_key":"value"})
    time.sleep(0.05)
    assert q, "expected a config_updated event"

def test_layout_publish_list_rollback():
    app = import_app()
    c = app.test_client()
    # Publish v1 for desktop
    rv = c.post("/api/v1/admin/layouts/publish", json={"breakpoint":"desktop","state":{"a":1}})
    assert rv.status_code == 200, rv.data
    v1 = rv.get_json()["version"]
    # Publish v2
    rv = c.post("/api/v1/admin/layouts/publish", json={"breakpoint":"desktop","state":{"a":2}})
    v2 = rv.get_json()["version"]
    assert v2 == v1 + 1
    # List
    rv = c.get("/api/v1/admin/layouts?breakpoint=desktop")
    items = rv.get_json()["items"]
    assert len(items) >= 2 and items[-1]["version"] == v2
    # Rollback to v1
    rv = c.post("/api/v1/admin/layouts/rollback", json={"breakpoint":"desktop","version":v1})
    assert rv.status_code == 200
    # Confirm current state is from v1
    rv = c.get("/api/v1/admin/layouts?breakpoint=desktop")
    items = rv.get_json()["items"]
    assert items[-1]["version"] > v2  # new version created by rollback publish
    # and last state's 'a' equals 1
    assert items[-1]["state"]["a"] == 1

def test_profile_gate_persisted():
    app = import_app()
    c = app.test_client()
    # Fresh login for a new user
    c.post("/api/v1/auth/login", json={"email":"phase4@demo.test"})
    # get profile -> not exists
    rv = c.get("/api/v1/profile/get")
    j = rv.get_json()
    assert j["ok"] is True and j["exists"] is False
    # save
    rv = c.post("/api/v1/profile/save", json={"name":"P4 User","title":"Tester"})
    assert rv.get_json()["ok"] is True
    # get -> exists
    rv = c.get("/api/v1/profile/get")
    assert rv.get_json()["exists"] is True

def test_users_and_memory_endpoints():
    app = import_app()
    c = app.test_client()
    # Use a named session and add a couple of turns via chat to create a transcript
    sid = "p4-session-1"
    c.post("/api/v1/chat", json={"session_id":sid, "text":"Hello Chip"})
    c.post("/api/v1/chat", json={"session_id":sid, "text":"Can you help?"})
    # List users
    rv = c.get("/api/v1/admin/users")
    assert rv.status_code == 200
    users = rv.get_json()["items"]
    assert any(u["email"] for u in users), "expected at least one user"
    # List sessions
    rv = c.get(f"/api/v1/admin/sessions?user=user@example.com")
    sessions = rv.get_json()["items"]
    assert any(s["id"] == sid for s in sessions), "expected our session"
    # Session detail
    rv = c.get(f"/api/v1/admin/session/{sid}")
    detail = rv.get_json()
    assert detail["ok"] is True and "transcript" in detail
    # Export/email transcript
    rv = c.post(f"/api/v1/admin/session/{sid}/email", json={"to":"someone@test"})
    assert rv.get_json().get("emailed") is True
    # Anonymize
    rv = c.post(f"/api/v1/admin/session/{sid}/anonymize")
    assert rv.get_json()["ok"] is True

def test_persistence_survives_reload(monkeypatch):
    # Simulate a "restart": wipe in-memory, then load from DAL
    dal = importlib.import_module("app.dal.neon_pg")
    dbm = importlib.import_module("app.db")
    # Write some config & a layout
    app = import_app()
    c = app.test_client()
    c.post("/api/v1/admin/config/update", json={"updates":{"theme":"dark"}})
    c.post("/api/v1/admin/layouts/publish", json={"breakpoint":"tablet","state":{"x":1}})
    # Simulate memory wipe
    dbm.db.memory = {'configs':{},'users':{},'profiles':{},'sessions':{},'emails':[],'logs':[],'layouts':{},'personas':{}}
    # reload from DAL
    dal.load_all_into_memory(dbm.db.memory)
    # Verify we got our values back
    assert dbm.db.memory.get('configs',{}).get('theme') == 'dark'
    assert dbm.db.memory.get('layouts',{}).get('tablet',{}).get('state',{}).get('x') == 1
