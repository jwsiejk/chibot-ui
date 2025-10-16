import time
from app.asgi_gateway import app as flask_app


def _with_admin_session(client):
    with client.session_transaction() as sess:
        sess["user"] = {"email": "admin@example.com"}


def test_admin_config_and_broadcast(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")
    c = flask_app.test_client()
    _with_admin_session(c)

    before = c.get('/api/v1/admin/logs').get_json()
    rv = c.post('/api/v1/admin/config', json={"suggestions_max_items": 3, "confirm_ms": 500})
    assert rv.status_code == 200
    cfg = rv.get_json()['config']
    assert cfg['suggestions_max_items'] == 3
    assert cfg['confirm_ms'] == 500

    after = c.get('/api/v1/admin/logs').get_json()
    new_events = after['events'][len(before['events']):]
    kinds = {evt.get('kind') for evt in new_events}
    assert 'config_update' in kinds or 'audit' in kinds


def test_layout_publish_and_rollback_broadcast(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")
    c = flask_app.test_client()
    _with_admin_session(c)

    before = c.get('/api/v1/admin/logs').get_json()
    rv = c.post('/api/v1/admin/layouts', json={"breakpoint":"desktop","json":{"grid":"v1"}})
    assert rv.status_code == 200
    state = rv.get_json()['state']
    assert state['published']['version'] == 1
    rv2 = c.post('/api/v1/admin/layouts', json={"breakpoint":"desktop","json":{"grid":"v2"}})
    assert rv2.get_json()['state']['published']['version'] == 2
    rb = c.post('/api/v1/admin/layouts/rollback', json={"breakpoint":"desktop","version":1})
    assert rb.get_json()['state']['published']['version'] == 1

    after = c.get('/api/v1/admin/logs').get_json()
    new_events = after['events'][len(before['events']):]
    kinds = {evt.get('kind') for evt in new_events}
    assert 'layout_publish' in kinds or 'layout_rollback' in kinds

def test_users_memory_list_export_email_anonymize():
    c = flask_app.test_client()
    # create a small conversation to populate session + users
    stream = c.get('/ws/v1/chat?session_id=pm4')
    c.get('/api/v1/greet?session_id=pm4')
    c.post('/api/v1/chat', json={'session_id':'pm4','text':'hello User user@example.com'})
    # list users
    rv = c.get('/api/v1/admin/users')
    assert rv.status_code == 200 and isinstance(rv.get_json()['users'], list)
    # list sessions
    rv2 = c.get('/api/v1/admin/sessions')
    sessions = rv2.get_json()['sessions']
    assert any(s['id']=='pm4' for s in sessions)
    # get session transcript
    rv3 = c.get('/api/v1/admin/sessions/pm4')
    assert 'transcript' in rv3.get_json()
    # export transcript
    rv4 = c.post('/api/v1/admin/sessions/pm4/export')
    assert rv4.headers.get('Content-Type').startswith('application/json')
    # email transcript
    rv5 = c.post('/api/v1/admin/sessions/pm4/email', json={'email':'owner@example.com'})
    assert rv5.get_json()['ok'] is True
    # anonymize
    rv6 = c.post('/api/v1/admin/sessions/pm4/anonymize')
    assert rv6.get_json()['ok'] is True
    # ensure transcript text redacted
    rv7 = c.get('/api/v1/admin/sessions/pm4')
    assert '[redacted@email]' in rv7.get_json()['transcript']
