import io
from app.asgi_gateway import app as flask_app
from app.ws.bus import bus
def test_voice_turn_and_tts_visemes():
    c = flask_app.test_client(); sid="p8"; q=bus.subscribe(sid)
    data={'file':(io.BytesIO(b'123'),'a.webm'),'mime':'audio/webm','meta':'{}','session_id':sid}
    assert c.post('/api/v1/voice/stt', data=data, content_type='multipart/form-data').status_code==200
    types=[f['type'] for f in [q.get(timeout=0.2) for _ in range(5)]]
    assert 'text'in types and 'audio_chunk'in types
    js= c.post('/api/v1/voice/tts-with-visemes', json={'text':'hi'}).get_json()
    assert js['ok'] and isinstance(js['visemes'], list) and 'audio_b64'in js
