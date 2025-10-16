import io, time, os
from queue import Empty
from app.asgi_gateway import app as flask_app
from app.ws.bus import bus
from app.db import db

def _drain(q, max_ms=1500):
    out=[]; t0=time.time()
    while (time.time()-t0)*1000<max_ms:
        try: out.append(q.get(timeout=0.05))
        except Empty: break
    return out
def _csrf(c): return c.get('/api/v1/auth/csrf').get_json()['csrf']
def _login(c, email):
    tok = _csrf(c); c.post('/api/v1/auth/login', json={'email':email}, headers={'X-CSRF-Token': tok})

def test_start_greet_streams_audio_and_suggestions():
    c = flask_app.test_client(); sid="p9start"; q=bus.subscribe(sid)
    assert c.get('/ws/v1/chat').status_code==426
    assert c.get(f'/api/v1/greet?session_id={sid}').status_code==200
    types=[f['type'] for f in _drain(q,1200)]
    assert 'audio_chunk'in types and 'suggestions'in types and 'end'in types and types[0]=='state' and types[-1]=='state'

def test_voice_turn_and_barge_in_interrupt():
    c = flask_app.test_client(); sid="p9voice"; q=bus.subscribe(sid)
    data={'file':(io.BytesIO(b'abc'),'v.webm'),'mime':'audio/webm','meta':'{}','session_id':sid}
    c.post('/api/v1/voice/stt', data=data, content_type='multipart/form-data')
    types=[f['type'] for f in _drain(q,1200)]; assert 'audio_chunk'in types
    q2=bus.subscribe(sid)
    rv=c.post('/api/v1/chat', json={'session_id':sid,'text':'long'}); tid=rv.get_json()['turn_id']
    c.post('/api/v1/chat', json={'session_id':sid,'cmd':'interrupt','turn_id':tid})
    frames=_drain(q2,1200)
    assert not [f for f in frames if f.get('type')=='end' and f.get('turn_id')==tid]
    assert any(f.get('type')=='state' and f.get('phase')=='ready' for f in frames)

def test_nudge_suggestions_email_and_admin_sse(monkeypatch):
    # Admin gating
    monkeypatch.setenv("ADMIN_EMAILS","jwsiejk@purestorage.com")
    c = flask_app.test_client()
    # greet to get suggestions & nudge path
    sid="p9nudge"; q=bus.subscribe(sid); c.get(f'/api/v1/greet?session_id={sid}')
    frames=_drain(q,1000); sug=[f for f in frames if f.get('type')=='suggestions'][-1]
    label=sug['items'][0]['label']
    c.post('/api/v1/chat', json={'session_id':sid,'text':label})
    # nudge backoff
    r1=c.post('/api/v1/chat', json={'session_id':sid,'cmd':'nudge'}).get_json()
    r2=c.post('/api/v1/chat', json={'session_id':sid,'cmd':'nudge'}).get_json()
    r3=c.post('/api/v1/chat', json={'session_id':sid,'cmd':'nudge'}).get_json()
    assert r1['nudged'] and r2['nudged'] and (not r3['nudged'])
    # End emails transcript to user
    tok=c.get('/api/v1/auth/csrf').get_json()['csrf']
    c.post('/api/v1/auth/login', json={'email':'jwsiejk@purestorage.com'}, headers={'X-CSRF-Token': tok})
    end=c.post('/api/v1/chat', json={'session_id':sid,'cmd':'end_session'}, headers={'X-CSRF-Token': tok}).get_json()
    assert end['emailed'] is True and any(e['to']=='jwsiejk@purestorage.com' for e in db.list_emails())
    # Admin log feed + actions
    logs_before=c.get('/api/v1/admin/logs').get_json()
    c.post('/api/v1/admin/config', json={'suggestions_max_items':3}, headers={'X-CSRF-Token': tok})
    c.post('/api/v1/admin/layouts', json={'breakpoint':'desktop','json':{'grid':'v1'}})
    c.post('/api/v1/admin/layouts/rollback', json={'breakpoint':'desktop','version':1})
    logs_after = c.get('/api/v1/admin/logs').get_json()
    new_events = logs_after['events'][len(logs_before['events']):]
    kinds = {evt.get('kind') for evt in new_events}
    assert kinds & {'config_update', 'layout_publish', 'layout_rollback', 'audit'}
    # Users & Memory
    ls=c.get('/api/v1/admin/sessions').get_json()['sessions']
    assert any(s['id']==sid for s in ls)
