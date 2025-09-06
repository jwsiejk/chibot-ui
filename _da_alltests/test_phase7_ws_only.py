import time
from queue import Empty
from app.asgi_gateway import app as flask_app
from app.ws.bus import bus
def _drain(q, max_ms=800):
    out=[]; import time as _t; t0=_t.time()
    while (_t.time()-t0)*1000<max_ms:
        try: out.append(q.get(timeout=0.05))
        except Empty: break
    return out
def test_ws_path_is_upgrade_only():
    c = flask_app.test_client(); r = c.get('/ws/v1/chat')
    assert r.status_code == 426 and r.headers.get('Upgrade') == 'websocket'
def test_greet_and_chat_enqueue_frames():
    c = flask_app.test_client(); sid="ws7x"; q = bus.subscribe(sid)
    g = c.get(f'/api/v1/greet?session_id={sid}'); assert g.status_code == 200
    frames = _drain(q, 1000); types=[f['type'] for f in frames]; order=[t for t in types if t in ('state','text','audio_chunk','suggestions','end')]
    assert order[0]=='state' and order[-1]=='state' and 'text'in order and 'audio_chunk'in order and 'suggestions'in order and 'end'in order
    q2 = bus.subscribe(sid); rv = c.post('/api/v1/chat', json={'session_id': sid, 'text': 'hello'}); assert rv.status_code == 200
    types2=[f['type'] for f in _drain(q2, 1000)]; assert 'text'in types2 and 'end'in types2
