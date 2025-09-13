
import os
os.environ.pop('DATABASE_URL', None)  # in-memory path

from app import create_app

def _csrf(c):
    r = c.get('/api/v1/csrf')
    return {'X-CSRF-Token': r.headers.get('X-CSRF-Token')}

def test_save_profile_roundtrip():
    app = create_app(); c = app.test_client()
    r = c.post('/api/v1/auth/login', json={'email':'alice@example.com'}, headers=_csrf(c)); assert r.status_code==200
    r = c.get('/api/v1/auth/me'); j = r.get_json(); assert j['authenticated'] is True
    r = c.post('/api/v1/profile', json={'name':'Alice','title':'SE','region':'West'}, headers=_csrf(c)); assert r.status_code==200
    r = c.get('/api/v1/auth/me'); j = r.get_json()
    assert j['profile_complete'] is True
    assert (j.get('profile') or {}).get('name') == 'Alice'
