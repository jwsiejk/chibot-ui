import os, json, pytest

os.environ['ADMIN_EMAILS'] = 'jwsiejk@purestorage.com'

def app_client():
    import importlib
    mod = importlib.import_module('app')
    if hasattr(mod, 'create_app'):
        a = mod.create_app(testing=True)
    else:
        a = mod.app
    a.config['TESTING'] = True
    return a.test_client()

def test_login_sets_session_and_me_profile():
    c = app_client()
    r = c.post('/api/v1/auth/login', json={'email': 'jwsiejk@purestorage.com'})
    assert r.status_code == 200
    r = c.get('/api/v1/auth/me')
    data = r.get_json()
    assert data['ok'] is True
    assert data['email'] == 'jwsiejk@purestorage.com'
    assert data['profile_complete'] in (False, True)

def test_profile_save_enables_profile_complete():
    c = app_client()
    c.post('/api/v1/auth/login', json={'email': 'jwsiejk@purestorage.com'})
    r = c.post('/api/v1/auth/profile/save', json={'name':'James', 'completed': True})
    assert r.status_code == 200
    r = c.get('/api/v1/auth/me')
    data = r.get_json()
    assert data['profile_complete'] is True

def test_admin_logs_ui_requires_admin():
    c = app_client()
    # Not logged in -> should 403 (no header)
    r = c.get('/api/v1/admin/logs-ui')
    assert r.status_code == 403
    # Login as admin email -> now allowed
    c.post('/api/v1/auth/login', json={'email': 'jwsiejk@purestorage.com'})
    r = c.get('/api/v1/admin/logs-ui')
    assert r.status_code == 200
    assert b'Admin Live Log' in r.data
