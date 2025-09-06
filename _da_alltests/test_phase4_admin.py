import json, time
from app.asgi_gateway import app as flask_app

def _read_sse(resp_bytes: bytes):
    text = resp_bytes.decode('utf-8', errors='ignore')
    events = []
    for block in [b for b in text.split('\n\n') if b.strip()]:
        lines = block.split('\n')
        ev = None
        payload = None
        for ln in lines:
            if ln.startswith('event: '):
                ev = ln[len('event: '):].strip()
            if ln.startswith('data: '):
                payload = ln[len('data: '):]
        if ev and payload:
            try:
                events.append((ev, json.loads(payload)))
            except Exception:
                events.append((ev, payload))
    return events

def test_admin_config_and_broadcast():
    c = flask_app.test_client()
    stream = c.get('/api/v1/admin/logs')
    rv = c.post('/api/v1/admin/config', json={"suggestions_max_items": 3, "confirm_ms": 500})
    assert rv.status_code == 200
    cfg = rv.get_json()['config']
    assert cfg['suggestions_max_items'] == 3
    assert cfg['confirm_ms'] == 500
    # SSE should include config_updated
    events = _read_sse(stream.data)
    kinds = [e[0] for e in events]
    assert 'config_updated' in kinds or 'audit' in kinds

def test_layout_publish_and_rollback_broadcast():
    c = flask_app.test_client()
    stream = c.get('/api/v1/admin/logs')
    rv = c.post('/api/v1/admin/layouts', json={"breakpoint":"desktop","json":{"grid":"v1"}})
    assert rv.status_code == 200
    state = rv.get_json()['state']
    assert state['published']['version'] == 1
    rv2 = c.post('/api/v1/admin/layouts', json={"breakpoint":"desktop","json":{"grid":"v2"}})
    assert rv2.get_json()['state']['published']['version'] == 2
    # Roll back to v1
    rb = c.post('/api/v1/admin/layouts/rollback', json={"breakpoint":"desktop","version":1})
    assert rb.get_json()['state']['published']['version'] == 1
    events = _read_sse(stream.data)
    kinds = [e[0] for e in events]
    assert 'layout_updated' in kinds

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
