
import os
os.environ.pop('DATABASE_URL', None)

from app import create_app

def _csrf(c):
    r = c.get('/api/v1/csrf')
    return {'X-CSRF-Token': r.headers.get('X-CSRF-Token')}

def test_login_prefill_save_complete():
    app = create_app()
    c = app.test_client()

    # Not logged in
    r = c.get('/api/v1/auth/me'); j = r.get_json()
    assert j['authenticated'] is False

    # Login
    r = c.post('/api/v1/auth/login', json={'email':'jwsiejk@purestorage.com'}, headers=_csrf(c))
    assert r.status_code == 200

    # me reflects session email; profile not complete yet
    r = c.get('/api/v1/auth/me'); j = r.get_json()
    assert j['authenticated'] is True
    assert j['email'] == 'jwsiejk@purestorage.com'
    assert j.get('profile_complete') in (False, None)

    # Save profile
    r = c.post('/api/v1/profile', json={'name':'James Siejk','title':'PTM','region':'PA'}, headers=_csrf(c))
    assert r.status_code == 200

    # me is now complete and returns the profile
    r = c.get('/api/v1/auth/me'); j = r.get_json()
    assert j['authenticated'] is True and j['profile_complete'] is True
    assert (j.get('profile') or {}).get('name') == 'James Siejk'
