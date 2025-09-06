import asyncio, threading, time, json
from urllib.parse import urlencode
from app.asgi_gateway import app as flask_app, asgi as asgi_app

class WSClient:
    def __init__(self, asgi_app, path):
        self.asgi_app = asgi_app
        self.path = path
        self.sent = []
        self._disconnect = False

    async def _receive(self):
        if not hasattr(self, '_connected'):
            self._connected = True
            return {'type':'websocket.connect'}
        for _ in range(40):
            if self._disconnect:
                return {'type':'websocket.disconnect'}
            await asyncio.sleep(0.05)
        return {'type':'websocket.disconnect'}

    async def _send(self, message):
        self.sent.append(message)

    async def run(self):
        if '?' in self.path:
            path, query = self.path.split('?', 1)
            query_string = query.encode('utf-8')
        else:
            path, query_string = self.path, b''
        scope = {
            'type':'websocket',
            'asgi': {'version':'3.0'},
            'path': path,
            'raw_path': path.encode('utf-8'),
            'query_string': query_string,
            'headers': [(b'host', b'testserver')],
            'client': ('127.0.0.1', 12345),
            'server': ('testserver', 80),
            'scheme': 'ws',
            'subprotocols': [],
        }
        await self.asgi_app(scope, self._receive, self._send)

    def start(self):
        self.thread = threading.Thread(target=lambda: asyncio.run(self.run()), daemon=True)
        self.thread.start()

    def stop(self):
        self._disconnect = True
        self.thread.join(timeout=3)

def _extract_text_frames(sent):
    texts = []
    for m in sent:
        if m['type'] == 'websocket.send' and 'text' in m:
            texts.append(m['text'])
    return texts

def test_ws_upgrade_and_greet_stream():
    ws = WSClient(asgi_app, '/ws/v1/chat?session_id=ws1')
    ws.start()
    time.sleep(0.15)
    client = flask_app.test_client()
    rv = client.get('/api/v1/greet?session_id=ws1')
    assert rv.status_code == 200
    time.sleep(0.4)
    ws.stop()
    frames = _extract_text_frames(ws.sent)
    assert any('"type":"state"' in t and '"ready"' in t for t in frames)
    assert any('"type":"text"' in t for t in frames)
    assert any('"type":"audio_chunk"' in t for t in frames)
    assert any('"type":"suggestions"' in t for t in frames)
    assert any('"type":"end"' in t for t in frames)

def test_admin_config_get_post_and_stream():
    client = flask_app.test_client()
    # Open a stream to prime, ignore first payload
    sse1 = client.get('/api/v1/admin/config/stream')
    _ = sse1.data.decode('utf-8', errors='ignore')
    # Update config
    rv = client.post('/api/v1/admin/config', json={'nudge_delay_ms': 5000, 'suggestions_max_items': 3})
    assert rv.status_code == 200
    # Open a fresh stream to capture the latest broadcasted state
    sse2 = client.get('/api/v1/admin/config/stream')
    text = sse2.data.decode('utf-8', errors='ignore')
    assert 'event: config_updated' in text
    assert '"nudge_delay_ms": 5000' in text
    assert '"suggestions_max_items": 3' in text
    # Confirm GET reflects change
    rv2 = client.get('/api/v1/admin/config')
    data = rv2.get_json()
    assert data['ok'] is True
    assert data['config']['nudge_delay_ms'] == 5000
    assert data['config']['suggestions_max_items'] == 3
