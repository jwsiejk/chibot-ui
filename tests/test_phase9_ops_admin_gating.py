import os
from app.asgi_gateway import app as flask_app

def _csrf(c):
    return c.get('/api/v1/auth/csrf').get_json()['csrf']

def _login(c, email):
    tok = _csrf(c)
    return c.post('/api/v1/auth/login', json={'email': email}, headers={'X-CSRF-Token': tok})

def test_admin_allowlist(monkeypatch):
    c = flask_app.test_client()
    # Gate to two emails
    monkeypatch.setenv("ADMIN_EMAILS", "jwsiejk@purestorage.com; allowed@example.com")
    # Not allowed
    _login(c, "someone@else.com")
    r = c.get('/api/v1/admin/sessions')
    assert r.status_code == 403
    # Allowed
    _login(c, "jwsiejk@purestorage.com")
    r2 = c.get('/api/v1/admin/sessions')
    assert r2.status_code == 200 and r2.get_json()['ok']

def test_whoami_and_healthz(monkeypatch):
    c = flask_app.test_client()
    monkeypatch.setenv("ADMIN_EMAILS", "admin@ex.com")
    # before login -> not admin
    w1 = c.get('/api/v1/auth/whoami').get_json()
    assert w1['ok'] and (w1['email'] is not None)  # defaults to user@example.com in this harness
    # login as admin -> is_admin true
    tok = _csrf(c)
    c.post('/api/v1/auth/login', json={'email':'admin@ex.com'}, headers={'X-CSRF-Token': tok})
    w2 = c.get('/api/v1/auth/whoami').get_json()
    assert w2['is_admin'] is True
    # healthz
    h = c.get('/api/v1/auth/healthz').get_json()
    assert h['ok'] and h['status'] == 'healthy'
