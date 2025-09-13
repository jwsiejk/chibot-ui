
def _csrf_headers(client):
    r = client.get("/api/v1/csrf")
    return {"X-CSRF-Token": r.headers.get("X-CSRF-Token")}

from app import create_app

def test_profile_gate_login_save_me():
    app = create_app()
    client = app.test_client()

    r = client.post("/api/v1/auth/login", json={"email":"jane.doe@example.com"}, headers=_csrf_headers(client))
    assert r.status_code == 200

    r = client.get("/api/v1/auth/me")
    j = r.get_json()
    assert j["authenticated"] is True
    assert j["profile_complete"] in (False, None)

    r = client.post("/api/v1/profile", json={
        "name":"Jane Doe",
        "title":"SE",
        "region":"Central"
    }, headers=_csrf_headers(client))
    assert r.status_code == 200

    r = client.get("/api/v1/auth/me")
    j = r.get_json()
    assert j["authenticated"] is True
    assert j["profile_complete"] is True
    assert (j["profile"] or {}).get("name") == "Jane Doe"
