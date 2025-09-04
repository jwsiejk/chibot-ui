import asyncio
from typing import Callable
from .ws.ws_asgi import ws_chat

async def asgi(scope, receive, send):
    if scope['type'] == 'websocket' and scope.get('path') == '/ws/v1/chat':
        await ws_chat(scope, receive, send)
        return
    # Minimal 404 for non-WS usage through this ASGI entrypoint
    if scope['type'] == 'http':
        await send({'type':'http.response.start','status':404,'headers':[(b'content-type', b'application/json')]})
        await send({'type':'http.response.body','body': b'{"ok": false, "error":"not_found"}'})
        return
    # lifespan or other events: no-op
    if scope['type'] == 'lifespan':
        while True:
            msg = await receive()
            if msg['type'] == 'lifespan.startup':
                await send({'type':'lifespan.startup.complete'})
            elif msg['type'] == 'lifespan.shutdown':
                await send({'type':'lifespan.shutdown.complete'})
                break
