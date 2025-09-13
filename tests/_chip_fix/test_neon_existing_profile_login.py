
import os
os.environ['DATABASE_URL'] = 'sqlite:////tmp/askchip.sqlite3'

from app import create_app
from app.dal.neon_pg import save_profile

def _csrf(c):
    r = c.get('/api/v1/csrf')
    return {'X-CSRF-Token': r.headers.get('X-CSRF-Token')}

def test_login_uses_existing_profile_from_db_case_insensitive():
    save_profile('JWSIEJK@PURESTORAGE.COM', {
        'email':'JWSIEJK@PURESTORAGE.COM',
        'name':'James',
        'title':'PTM',
        'region':'East',
        'profile_complete': True
    })
    app = create_app()
    c = app.test_client()
    r = c.post('/api/v1/auth/login', json={'email':'jwsiejk@purestorage.com'}, headers=_csrf(c))
    assert r.status_code == 200

    r = c.get('/api/v1/auth/me')
    j = r.get_json()
    assert j['authenticated'] is True
    assert j['email'].lower() == 'jwsiejk@purestorage.com'
    assert j['profile_complete'] is True
    assert (j.get('profile') or {}).get('name') == 'James'
