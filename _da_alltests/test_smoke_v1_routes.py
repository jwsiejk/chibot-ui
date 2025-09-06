from app.asgi_gateway import app as flask_app
import io

def test_greet():
    client = flask_app.test_client()
    rv = client.get('/api/v1/greet')
    assert rv.status_code == 200, rv.data
    data = rv.get_json()
    assert data['ok'] is True
    assert 'turn_id' in data

def test_chat_post():
    client = flask_app.test_client()
    rv = client.post('/api/v1/chat', json={'text': 'hello'})
    assert rv.status_code == 200
    data = rv.get_json()
    assert data['ok'] is True
    assert 'turn_id' in data

def test_voice_stt_multipart_and_stream_enqueue():
    client = flask_app.test_client()
    stream = client.get('/ws/v1/chat?session_id=voice1')
    data = {
        'file': (io.BytesIO(b'123'), 'blob.webm'),
        'mime': 'audio/webm',
        'meta': '{}',
        'session_id': 'voice1'
    }
    rv = client.post('/api/v1/voice/stt', data=data, content_type='multipart/form-data')
    assert rv.status_code == 200
    data = rv.get_json()
    assert data['ok'] is True
    assert 'turn_id' in data and 'text' in data
    # stream should contain assistant frames
    sse_text = stream.data.decode('utf-8', errors='ignore')
    assert 'data: {' in sse_text

def test_tts_with_visemes():
    client = flask_app.test_client()
    rv = client.post('/api/v1/voice/tts-with-visemes', json={'text': 'hello'})
    assert rv.status_code == 200
    data = rv.get_json()
    assert data['ok'] is True
    assert 'audio_b64' in data
    assert 'visemes' in data and isinstance(data['visemes'], list)

def test_ws_stream_sse():
    client = flask_app.test_client()
    rv = client.get('/ws/v1/chat?session_id=smoke')
    assert rv.status_code == 200
    assert rv.headers.get('Content-Type').startswith('text/event-stream')
