import asyncio, json
from app.asgi_gateway import asgi, app as flask_app

class WSClient:
    def __init__(self, path):
        self.path = path
        self.sent = []
        self.received = []
        self._recv_queue = asyncio.Queue()

    async def send_client(self, message):
        await self._recv_queue.put(message)

    async def _receive(self):
        if self._first:
            self._first = False
            return {'type': 'websocket.connect'}
        return await self._recv_queue.get()

    async def _send(self, message):
        self.sent.append(message)
        if message.get('type') == 'websocket.send':
            if 'text' in message:
                self.received.append(json.loads(message['text']))

    async def run(self, query='session_id=w1'):
        scope = {
            'type': 'websocket',
            'path': self.path,
            'query_string': query.encode('utf-8'),
            'headers': []
        }
        self._first = True
        task = asyncio.create_task(asgi(scope, self._receive, self._send))
        await asyncio.sleep(0.05)
        return task

async def _ws_collect_until_ready(client, task):
    # wait for at least one state frame then trigger greet via HTTP
    await asyncio.sleep(0.05)

def test_ws_upgrade_and_stream_from_http_greet():
    client = flask_app.test_client()
    async def scenario():
        ws = WSClient('/ws/v1/chat')
        task = await ws.run('session_id=w2')
        # now trigger greet which should enqueue frames to bus
        rv = client.get('/api/v1/greet?session_id=w2')
        assert rv.status_code == 200
        await asyncio.sleep(0.3)
        # close connection
        await ws.send_client({'type': 'websocket.disconnect', 'code': 1000})
        await asyncio.wait_for(task, timeout=2.0)
        # verify we received at least one assistant text frame
        types = [m.get('type') for m in ws.received]
        assert 'state' in types and 'text' in types and 'end' in types
    asyncio.run(scenario())

def test_ws_user_text_and_interrupt():
    async def scenario():
        ws = WSClient('/ws/v1/chat')
        task = await ws.run('session_id=w3')
        # send a user_text frame
        await ws.send_client({'type':'websocket.receive','text': json.dumps({'type':'user_text','text':'hello'})})
        await asyncio.sleep(0.2)
        # collect some types
        types = [m.get('type') for m in ws.received]
        assert 'text' in types and 'end' in types
        # send an interrupt
        # pick the last seen turn_id from text frame if present
        tid = None
        for m in ws.received:
            if m.get('type') == 'text':
                tid = m.get('turn_id')
        await ws.send_client({'type':'websocket.receive','text': json.dumps({'type':'control','cmd':'interrupt','turn_id':tid})})
        await asyncio.sleep(0.05)
        await ws.send_client({'type': 'websocket.disconnect', 'code': 1000})
        await asyncio.wait_for(task, timeout=2.0)
    asyncio.run(scenario())

def test_rate_limit_chat_and_stt():
    client = flask_app.test_client()
    # chat: 6 quick posts should rate-limit at >5
    ok = 0; limited = 0
    for i in range(6):
        rv = client.post('/api/v1/chat', json={'session_id':'rl1','text':'hi'})
        if rv.status_code == 429:
            limited += 1
            assert 'retry_after_ms' in rv.get_json()
        else:
            ok += 1
    assert ok >= 5 and limited >= 1
    # stt: 5 quick posts should rate-limit at >4
    import io
    ok = 0; limited = 0
    for i in range(5):
        data = {'file': (io.BytesIO(b'123'), 'blob.webm'), 'mime': 'audio/webm', 'meta': '{}', 'session_id':'rl2'}
        rv = client.post('/api/v1/voice/stt', data=data, content_type='multipart/form-data')
        if rv.status_code == 429:
            limited += 1
            assert 'retry_after_ms' in rv.get_json()
        else:
            ok += 1
    assert ok >= 4 and limited >= 1
