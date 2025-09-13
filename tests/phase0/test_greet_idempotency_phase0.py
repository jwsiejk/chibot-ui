
from app import create_app

def test_greet_idempotent_without_vendor_dependency():
    app = create_app()
    client = app.test_client()
    # single session id under test; server should return same turn_id twice
    r1 = client.get("/api/v1/greet?session_id=phase0test")
    assert r1.status_code in (200, 409), f"Unexpected status {r1.status_code}"
    j1 = r1.get_json()
    assert "turn_id" in j1, f"Missing 'turn_id' in response: {j1}"
    tid = j1["turn_id"]
    r2 = client.get("/api/v1/greet?session_id=phase0test")
    assert r2.status_code in (200, 409), f"Unexpected status {r2.status_code}"
    j2 = r2.get_json()
    assert "turn_id" in j2
    assert j2["turn_id"] == tid, "Second greet should return same turn_id for same session"
