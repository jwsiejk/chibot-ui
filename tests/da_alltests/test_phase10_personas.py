import os
from app.asgi_gateway import app as flask_app
from app.db import db

def _csrf(c): return c.get('/api/v1/auth/csrf').get_json()['csrf']
def _login(c, email):
    tok = _csrf(c)
    c.post('/api/v1/auth/login', json={'email': email}, headers={'X-CSRF-Token': tok})
    return tok

def test_persona_crud_publish_assign_export_and_rollback(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "jwsiejk@purestorage.com")
    c = flask_app.test_client()
    # not logged in -> admin blocked
    assert c.get('/api/v1/admin/personas').status_code == 403

    tok = _login(c, "jwsiejk@purestorage.com")
    # list shows default 'chip'
    resp = c.get('/api/v1/admin/personas').get_json()
    assert any(p['id']=='chip' and p['published_version']>=1 for p in resp['personas'])

    # create a new persona 'oak'
    cfg_v1 = {"id":"oak","prompt":{"system":"Oak v1 system"},"policy":{"teacher_moves":True}}
    r_create = c.post('/api/v1/admin/personas', json={'config': cfg_v1}, headers={'X-CSRF-Token': tok}).get_json()
    assert r_create['ok'] and r_create['id']=='oak'

    # import v2
    cfg_v2 = {"id":"oak","prompt":{"system":"Oak v2 system"}}
    r_imp = c.post('/api/v1/admin/personas/oak/import', json={'config': cfg_v2}, headers={'X-CSRF-Token': tok}).get_json()
    assert r_imp['ok'] and r_imp['version']==2

    # publish v2
    r_pub = c.post('/api/v1/admin/personas/oak/publish', json={}, headers={'X-CSRF-Token': tok}).get_json()
    assert r_pub['ok'] and r_pub['published_version']==2

    # assign to a session
    sid = "p10s1"
    r_asg = c.post('/api/v1/admin/personas/assign', json={'session_id': sid, 'persona_id':'oak'}, headers={'X-CSRF-Token': tok}).get_json()
    assert r_asg['ok'] and r_asg['persona_id']=='oak'

    # greet uses/keeps persona_id in session
    g = c.get(f'/api/v1/greet?session_id={sid}')
    assert g.status_code == 200
    assert db.memory['sessions'][sid]['persona_id'] == 'oak'

    # rollback to v1
    r_rb = c.post('/api/v1/admin/personas/oak/rollback', json={'version':1}, headers={'X-CSRF-Token': tok}).get_json()
    assert r_rb['ok'] and r_rb['published_version']==1

    # export returns published (v1) config
    exp = c.get('/api/v1/admin/personas/oak/export').get_json()
    assert exp['ok'] and exp['config']['prompt']['system']=="Oak v1 system"
