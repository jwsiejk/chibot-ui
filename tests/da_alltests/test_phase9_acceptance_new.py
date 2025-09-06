import os, json, io, time
from queue import Empty
from app.asgi_gateway import app as flask_app
from app.ws.bus import bus

def _drain(q, max_ms=1000):
    out = []
    t0 = time.time()
    while (time.time()-t0)*1000 < max_ms:
        try:
            out.append(q.get(timeout=0.05))
        except Empty:
            break
    return out

def _csrf(c):
    return c.get('/api/v1/auth/csrf').get_json()['csrf']

def _login(c, email):
    tok = _csrf(c)
    c.post('/api/v1/auth/login', json={'email': email}, headers={'X-CSRF-Token': tok})

def test_start_and_greet_streams_with_visemes():
    c = flask_app.test_client()
    r = c.get('/ws/v1/chat'); assert r.status_code == 426
    sid = "p9greet"
    q = bus.subscribe(sid)
    g = c.get(f'/api/v1/greet?session_id={sid}')
    assert g.status_code == 200
    frames = _drain(q)
    types = [f['type'] for f in frames]
    assert 'text' in types and 'audio_chunk' in types and 'end' in types
    t = c.post('/api/v1/voice/tts-with-visemes', json={'text':'hello'}).get_json()
    assert t['ok'] and isinstance(t['visemes'], list)

def test_voice_turn_stt_streams_back():
    c = flask_app.test_client()
    sid = "p9voice"
    q = bus.subscribe(sid)
    data = {'file': (io.BytesIO(b'123'), 'blob.webm'), 'mime':'audio/webm', 'meta':'{"vad":"on"}', 'session_id': sid}
    r = c.post('/api/v1/voice/stt', data=data, content_type='multipart/form-data')
    assert r.status_code == 200
    types = [f['type'] for f in _drain(q)]
    assert 'text' in types and 'audio_chunk' in types and 'end' in types

def test_soft_barge_in_interrupt_ready():
    c = flask_app.test_client()
    sid = "p9barge"
    q1 = bus.subscribe(sid)
    rv = c.post('/api/v1/chat', json={'session_id': sid, 'text':'speaking now'})
    assert rv.status_code == 200
    tid = rv.get_json()['turn_id']
    q2 = bus.subscribe(sid)
    r2 = c.post('/api/v1/chat', json={'session_id': sid, 'cmd': 'interrupt', 'turn_id': tid})
    assert r2.status_code == 200 and r2.get_json()['interrupted'] is True
    frames = _drain(q1) + _drain(q2)
    assert any(f.get('type')=='state' and f.get('phase')=='ready' for f in frames)

def test_nudge_backoff():
    c = flask_app.test_client()
    sid = "p9nudge"
    q = bus.subscribe(sid)
    n1 = c.post('/api/v1/chat', json={'session_id': sid, 'cmd':'nudge'}).get_json(); assert n1['nudged'] is True
    n2 = c.post('/api/v1/chat', json={'session_id': sid, 'cmd':'nudge'}).get_json(); assert n2['nudged'] is True
    n3 = c.post('/api/v1/chat', json={'session_id': sid, 'cmd':'nudge'}).get_json(); assert n3['nudged'] is False
    reasons = [f.get('reason') for f in _drain(q) if f.get('type')=='end']
    assert reasons.count('nudge') == 2

def test_suggestions_hygiene_and_followup():
    c = flask_app.test_client()
    sid = "p9sugg"
    q = bus.subscribe(sid)
    c.post('/api/v1/chat', json={'session_id': sid, 'text': 'give me steps'})
    items = [f for f in _drain(q) if f.get('type')=='suggestions'][-1]['items']
    assert len(items) <= 4 and all(len(i['label'].split()) <= 7 for i in items)
    q2 = bus.subscribe(sid)
    c.post('/api/v1/chat', json={'session_id': sid, 'text': items[0]['label']})
    assert any(f.get('type')=='text' for f in _drain(q2))

def test_transcript_emailed_on_end():
    c = flask_app.test_client()
    sid = "p9end"
    tok = c.get('/api/v1/auth/csrf').get_json()['csrf']
    c.post('/api/v1/auth/login', json={'email':'owner@example.com'}, headers={'X-CSRF-Token': tok})
    c.post('/api/v1/chat', json={'session_id': sid, 'text': 'hello'})
    end = c.post('/api/v1/chat', json={'session_id': sid, 'cmd':'end_session'}).get_json()
    assert end['ok'] and end['emailed']
    from app.db import db
    assert any(m['to']=='owner@example.com' for m in db.list_emails())

def test_admin_sse_layouts_users_audit(monkeypatch):
    c = flask_app.test_client()
    monkeypatch.setenv("ADMIN_EMAILS", "jwsiejk@purestorage.com")
    tok = c.get('/api/v1/auth/csrf').get_json()['csrf']
    c.post('/api/v1/auth/login', json={'email':'jwsiejk@purestorage.com'}, headers={'X-CSRF-Token': tok})
    sse = c.get('/api/v1/admin/logs')
    c.post('/api/v1/admin/config', json={'confirm_ms': 500})
    payload = sse.data.decode('utf-8', errors='ignore')
    from app.db import db
    assert 'heartbeat' in payload
    assert len(db.memory['logs']) >= 1  # audited
    sse2 = c.get('/api/v1/admin/logs')
    c.post('/api/v1/admin/layouts', json={'breakpoint':'desktop','json':{'grid':'v1'}})
    c.post('/api/v1/admin/layouts', json={'breakpoint':'desktop','json':{'grid':'v2'}})
    c.post('/api/v1/admin/layouts/rollback', json={'breakpoint':'desktop','version':1})
    payload2 = sse2.data.decode('utf-8', errors='ignore')
    assert 'heartbeat' in payload2
    assert len(db.memory['logs']) >= 3  # publish, publish, rollback
    c.post('/api/v1/chat', json={'session_id':'S1','text':'hello'})
    ls = c.get('/api/v1/admin/sessions').get_json(); assert any(s['id']=='S1' for s in ls['sessions'])
    ej = c.post('/api/v1/admin/sessions/S1/export.json')
    assert ej.status_code == 200 and ej.headers['Content-Type'].startswith('application/json')
