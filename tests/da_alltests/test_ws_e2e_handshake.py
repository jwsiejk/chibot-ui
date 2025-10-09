import asyncio, json
from app.services.streaming import run_ws_greet
from app.ws.bus import bus
from app.ws.ws_asgi import _ws_chat_asgi_impl
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

def test_ws_streams_after_greet(monkeypatch):
    async def _run():
        monkeypatch.setenv("WS_TOKEN_REQUIRED", "0")
        sid="wse2e"
        c=ASGIClient(_ws_chat_asgi_impl, "/ws/v1/chat", f"session_id={sid}".encode()); await c.start()
        http=flask_app.test_client(); http.get(f"/api/v1/greet?session_id={sid}")
        turn_id=run_ws_greet(sid)
        bus.broadcast(sid, {"type":"audio_chunk","turn_id":turn_id,"base64":""})
        await asyncio.sleep(0.2)
        types=[]
        for m in c.sent:
            if m.get('type')=='websocket.send' and 'text'in m:
                try:
                    fr=json.loads(m['text'])
                    t=fr.get('type')
                    if t=='assistant_chunk': t='text'
                    elif t=='assistant_end': t='end'
                    elif t=='assistant_audio': t='audio_chunk'
                    types.append(t)
                except Exception: pass
        assert 'state' in types and 'text' in types and 'audio_chunk' in types and 'suggestions' in types and 'end' in types
        await c.disconnect(); await c.wait()

    asyncio.run(_run())
