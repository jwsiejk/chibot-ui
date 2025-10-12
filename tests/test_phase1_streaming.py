import json
import threading
from app.asgi_gateway import app as flask_app
from app.api_v1 import greet as greet_api
from app.services import streaming as streaming

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

def test_greet_stream_sequence():
    client = flask_app.test_client()
    stream = client.get('/ws/v1/chat?session_id=t1')
    g = client.get('/api/v1/greet?session_id=t1')
    assert g.status_code == 200
    data = stream.data
    events = _sse_messages(data)
    types = [e.get('type') for e in events]
    order = [t for t in types if t in ('state','text','audio_chunk','suggestions','end')]
    assert order[0] == 'state'
    assert 'text' in order
    assert 'audio_chunk' in order
    assert 'suggestions' in order
    assert 'end' in order
    assert order[-1] == 'state'

def test_chat_turn_and_interrupt():
    client = flask_app.test_client()
    stream = client.get('/ws/v1/chat?session_id=t2')
    rv = client.post('/api/v1/chat', json={'session_id':'t2','text':'hello'})
    assert rv.status_code == 200
    tid = rv.get_json()['turn_id']
    rv2 = client.post('/api/v1/chat', json={'session_id':'t2','cmd':'interrupt','turn_id':tid})
    assert rv2.status_code == 200 and rv2.get_json()['interrupted'] is True
    data = stream.data
    events = _sse_messages(data)
    ready_states = [e for e in events if e.get('type')=='state' and e.get('phase')=='ready']
    assert ready_states, "Expected at least one ready state after interrupt"
    ended_tids = [e.get('turn_id') for e in events if e.get('type')=='end']
    assert tid not in ended_tids


def test_greet_admin_emits_single_assistant_end(monkeypatch):
    client = flask_app.test_client()
    sid = 'admin-greet-sse'

    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test')

    events = []
    done = threading.Event()

    def fake_make_assistant_frames(seed_text, session_id, **kwargs):
        frames = [
            {'type': 'assistant_chunk', 'turn_id': 'turn-admin', 'text': 'hello', 'kb_hits': 0},
        ]
        return 'turn-admin', frames

    def fake_admin_emit(kind, **payload):
        events.append((kind, payload))
        if kind == 'schedule_frames_done':
            done.set()
        return True

    monkeypatch.setattr(streaming, 'make_assistant_frames', fake_make_assistant_frames)
    monkeypatch.setattr(streaming, 'schedule_tts_audio', lambda *a, **k: False)
    monkeypatch.setattr(streaming, '_admin_emit', fake_admin_emit)
    monkeypatch.setattr(greet_api, '_admin_emit', fake_admin_emit)
    monkeypatch.setattr(streaming.bus, 'broadcast', lambda *a, **k: None)
    monkeypatch.setattr(streaming.bus, 'note_assistant_turn', lambda *a, **k: None)

    resp = client.get(f'/api/v1/greet?session_id={sid}')
    assert resp.status_code == 200
    assert done.wait(2.0), 'schedule_frames_done event did not arrive'

    assistant_end_events = [payload for kind, payload in events if kind == 'assistant_end']
    assert len(assistant_end_events) == 1, f'assistant_end emitted {len(assistant_end_events)} times'
