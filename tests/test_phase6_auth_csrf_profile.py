from app.asgi_gateway import app as flask_app
from app.db import db
def _csrf(client):
    r = client.get('/api/v1/auth/csrf'); assert r.status_code == 200; return r.get_json()['csrf']
def test_profile_gate_and_csrf_toggle_flow():
    c = flask_app.test_client()
    tok = _csrf(c); c.post('/api/v1/auth/login', json={'email':'tester@example.com'}, headers={'X-CSRF-Token': tok})
    r = c.post('/api/v1/admin/config', json={'profile_gate_enabled': True, 'csrf_enforced': True}, headers={'X-CSRF-Token': tok})
    assert r.status_code == 200 and r.get_json()['config']['profile_gate_enabled'] is True
    g = c.get('/api/v1/greet?session_id=p6'); assert g.status_code == 400 and g.get_json()['error'] == 'profile_required'
    tok = _csrf(c); sp = c.post('/api/v1/profile', json={'name':'Test User','title':'SE','region':'NA'}, headers={'X-CSRF-Token': tok})
    assert sp.status_code == 200 and sp.get_json()['profile']['name'] == 'Test User'
    g2 = c.get('/api/v1/greet?session_id=p6'); assert g2.status_code == 200
    bad = c.post('/api/v1/chat', json={'session_id':'p6','text':'hello'}); assert bad.status_code == 403
    tok = _csrf(c); ok = c.post('/api/v1/chat', json={'session_id':'p6','text':'hello'}, headers={'X-CSRF-Token': tok}); assert ok.status_code == 200
    tok = _csrf(c); end = c.post('/api/v1/chat', json={'session_id':'p6','cmd':'end_session'}, headers={'X-CSRF-Token': tok})
    assert end.status_code == 200 and end.get_json()['emailed'] is True
    emails = db.list_emails(); assert any(e['to'] == 'tester@example.com' for e in emails)
