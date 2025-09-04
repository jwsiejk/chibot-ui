import asyncio, json, urllib.parse, contextlib
from ..ws.bus import bus
async def ws_chat_app(scope, receive, send):
    if scope['type']!='websocket':
        await send({'type':'http.response.start','status':404,'headers':[]})
        await send({'type':'http.response.body','body':b'not found'}); return
    qs=scope.get('query_string') or b''
    import urllib.parse as _p; params=_p.parse_qs(qs.decode('utf-8'))
    sid=(params.get('session_id') or ['default'])[0]
    await send({'type':'websocket.accept'})
    q=bus.subscribe(sid)
    async def pump():
        from queue import Empty
        while True:
            try: fr=q.get(timeout=0.05)
            except Empty: await asyncio.sleep(0.01); continue
            await send({'type':'websocket.send','text':json.dumps(fr)})
    task=asyncio.create_task(pump())
    try:
        while True:
            ev=await receive()
            if ev['type']=='websocket.receive' and ev.get('text')=='close': break
            if ev['type']=='websocket.disconnect': break
    finally:
        task.cancel()
        with contextlib.suppress(Exception): await task
        await send({'type':'websocket.close'})
