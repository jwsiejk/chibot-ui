import json, asyncio, urllib.parse
from .bus import bus
from ..services.streaming import make_assistant_frames

async def ws_chat(scope, receive, send):
    assert scope['type'] == 'websocket'
    # Parse session_id from query string
    qs = scope.get('query_string', b'').decode('utf-8')
    params = urllib.parse.parse_qs(qs)
    session_id = (params.get('session_id',[None])[0]) or 'default'
    last_tid = None

    # Accept
    await send({'type':'websocket.accept'})
    # Initial ready state
    await send({'type':'websocket.send','text': json.dumps({"type":"state","phase":"ready"})})

    # Subscribe to the bus
    loop = asyncio.get_event_loop()
    q = bus.subscribe(session_id)

    async def forward_bus():
        while True:
            # Use a thread-safe queue via run_in_executor? The bus queue is threadsafe.
            frame = await loop.run_in_executor(None, q.get)
            # Drop late frames for canceled turns
            tid = frame.get('turn_id')
            if frame.get('type') in ('text','audio_chunk','end') and tid and bus.is_canceled(session_id, tid):
                continue
            await send({'type':'websocket.send','text': json.dumps(frame, separators=(',',':'))})
    forward_task = asyncio.create_task(forward_bus())

    try:
        while True:
            ev = await receive()
            t = ev['type']
            if t == 'websocket.disconnect':
                forward_task.cancel()
                break
            if t == 'websocket.receive':
                data = ev.get('text')
                if not data:
                    continue
                try:
                    msg = json.loads(data)
                except Exception:
                    continue
                mtype = msg.get('type')
                if mtype == 'control':
                    cmd = msg.get('cmd')
                    if cmd == 'interrupt' and last_tid:
                        bus.cancel_turn(session_id, last_tid)
                        await send({'type':'websocket.send','text': json.dumps({"type":"state","phase":"ready"})})
                    if cmd == 'nudge':
                        # server synthesizes a short nudge reply
                        _, frames = make_assistant_frames("Still with me? Want a quick recap?")
                        for fr in frames:
                            if fr.get('type') == 'end':
                                fr['reason'] = 'nudge'
                            await send({'type':'websocket.send','text': json.dumps(fr, separators=(',',':'))})
                elif mtype == 'user_text':
                    _, frames = make_assistant_frames(msg.get('text') or '')
                    # remember last tid
                    for fr in frames:
                        if fr.get('type') == 'text':
                            last_tid = fr.get('turn_id')
                        await send({'type':'websocket.send','text': json.dumps(fr, separators=(',',':'))})
    finally:
        try:
            forward_task.cancel()
        except Exception:
            pass
