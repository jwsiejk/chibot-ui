import asyncio, json, pytest
from app.ws.asgi_chat import ws_chat_app
from app.asgi_gateway import app as flask_app

class ASGIClient:
    def __init__(self, app, path, query_string=b''): self.app=app; self.path=path; self.qs=query_string; self.sent=[]; import asyncio as _a; self.q=_a.Queue(); self.task=None
    async def send(self, msg): self.sent.append(msg)
    async def receive(self): return await self.q.get()
    async def start(self):
        scope={'type':'websocket','asgi':{'version':'3.0'},'path':self.path,'query_string':self.qs}
        self.task=asyncio.create_task(self.app(scope, self.receive, self.send)); await self.q.put({'type':'websocket.connect'})
    async def push_text(self, t): await self.q.put({'type':'websocket.receive','text':t})
    async def disconnect(self): await self.q.put({'type':'websocket.disconnect'}); await asyncio.sleep(0.01); 
    async def wait(self): 
        if self.task: await self.task

@pytest.mark.asyncio
async def test_ws_streams_after_greet():
    sid="wse2e"
    c=ASGIClient(ws_chat_app, "/ws/v1/chat", f"session_id={sid}".encode()); await c.start()
    http=flask_app.test_client(); http.get(f"/api/v1/greet?session_id={sid}")
    await asyncio.sleep(0.2)
    types=[]
    for m in c.sent:
        if m.get('type')=='websocket.send' and 'text'in m:
            try: types.append(json.loads(m['text'])['type'])
            except Exception: pass
    assert 'state' in types and 'text' in types and 'audio_chunk' in types and 'suggestions' in types and 'end' in types
    await c.disconnect(); await c.wait()
