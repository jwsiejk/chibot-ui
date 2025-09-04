import json
import importlib, sys
importlib.invalidate_caches()
for m in list(sys.modules.keys()):
    if m == 'app' or m.startswith('app.'):
        del sys.modules[m]
from app.asgi_gateway import asgi as flask_app

def _sse_messages(resp_data: bytes):
    text = resp_data.decode('utf-8', errors='ignore')
    events = []
    for block in [b.strip() for b in text.split('\n\n') if b.strip()]:
        if block.startswith(':'):
            continue
        for line in block.split('\n'):
            if line.startswith('data: '):
                payload = line[len('data: '):]
                try:
                    events.append(json.loads(payload))
                except Exception:
                    pass
    return events

def _labels_ok(items):
    if len(items) > 4: return False
    for it in items:
        words = it.get('label','').strip().split()
        if len(words) > 7:
            return False
    return True

def test_interrupt_drops_late_frames():
    client = flask_app.test_client()
    stream = client.get('/ws/v1/chat?session_id=p2a')
    rv = client.post('/api/v1/chat', json={'session_id':'p2a','text':'talk to me'})
    tid = rv.get_json()['turn_id']
    client.post('/api/v1/chat', json={'session_id':'p2a','cmd':'interrupt','turn_id':tid})
    data = stream.data
    ev = _sse_messages(data)
    by_tid = [e for e in ev if e.get('turn_id') == tid]
    assert not any(e.get('type') == 'audio_chunk' for e in by_tid), f"audio_chunk leaked for {tid}"
    assert not any(e.get('type') == 'end' for e in by_tid), f"end leaked for {tid}"

def test_suggestions_contract_and_labels():
    client = flask_app.test_client()
    stream = client.get('/ws/v1/chat?session_id=p2b')
    client.post('/api/v1/chat', json={'session_id':'p2b','text':'hello'})
    data = stream.data
    ev = _sse_messages(data)
    suggs = [e for e in ev if e.get('type') == 'suggestions']
    assert suggs, "expected suggestions frame"
    assert all(_labels_ok(s.get('items', [])) for s in suggs), "suggestion labels/length invalid"

def test_nudge_with_backoff():
    client = flask_app.test_client()
    stream = client.get('/ws/v1/chat?session_id=p2c')
    client.post('/api/v1/chat', json={'session_id':'p2c','cmd':'nudge'})
    client.post('/api/v1/chat', json={'session_id':'p2c','cmd':'nudge'})
    client.post('/api/v1/chat', json={'session_id':'p2c','cmd':'nudge'})
    data = stream.data
    ev = _sse_messages(data)
    nudge_texts = [e for e in ev if e.get('type')=='text' and e.get('role')=='assistant' and ('chip' in e.get('content','').lower() or 'still with me' in e.get('content','').lower())]
    assert len(nudge_texts) >= 2
    n_end = sum(1 for e in ev if e.get('type')=='end' and e.get('reason')=='nudge')
    assert n_end <= 2, f"nudge backoff failed, end frames={n_end}"
