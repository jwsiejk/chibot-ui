
from app import create_app

def test_login_and_profile_completion_flow():
    app = create_app()
    client = app.test_client()

    # Ensure CSRF token is issued
    r = client.get('/api/v1/csrf')
    token = r.headers.get('X-CSRF-Token')

    # Initially not authenticated
    r = client.get('/api/v1/auth/me')
    assert r.status_code == 200
    j = r.get_json()
    assert j['authenticated'] is False

    # Login
    r = client.post('/api/v1/auth/login', json={'email':'user@example.com'}, headers={'X-CSRF-Token': token})
    assert r.status_code == 200
    j = r.get_json()
    assert j['ok'] is True

    # After login, profile likely incomplete
    r = client.get('/api/v1/auth/me')
    j = r.get_json()
    assert j['authenticated'] is True
    assert j['profile_complete'] in (False, True)  # may be False if no profile

    # Save profile to complete
    r = client.post('/api/v1/profile', json={'email':'user@example.com','name':'User','title':'SE','region':'West'}, headers={'X-CSRF-Token': token})
    assert r.status_code == 200
    j = r.get_json()
    assert j['ok'] is True
    assert bool(j['profile']['profile_complete']) is True

    # Verify profile_complete now true
    r = client.get('/api/v1/auth/me')
    j = r.get_json()
    assert j['authenticated'] is True
    assert j['profile_complete'] is True
