import json
import types
from app.ws.ws_asgi import ws_chat

async def _run(events):
    sent = []
    async def receive():
        return events.pop(0) if events else {'type':'websocket.disconnect'}
    async def send(msg):
        sent.append(msg)
    scope = {'type':'websocket','path':'/ws/v1/chat','query_string': b''}
    await ws_chat(scope, receive, send)
    return sent

def _texts(sent):
    return [m['text'] for m in sent if m.get('type')=='websocket.send' and 'text' in m]

def test_demux_and_keepalive_ack():
    events = [
        {'type':'websocket.receive','bytes': b'\x00\x01\x02'},  # binary should be buffered, not errored
        {'type':'websocket.receive','text': json.dumps({'type':'KeepAlive'})},
        {'type':'websocket.disconnect'}
    ]
    sent = __import__('asyncio').get_event_loop().run_until_complete(_run(events))
    # First message is accept
    assert any(m.get('type')=='websocket.accept' for m in sent), "WS not accepted"
    # Should ack KeepAlive
    texts = _texts(sent)
    assert any(json.loads(t).get('type')=='KeepAliveAck' for t in texts), "No KeepAliveAck"

def test_close_produces_final_and_utterance_end():
    events = [
        {'type':'websocket.receive','bytes': b'\x11\x22'},
        {'type':'websocket.receive','text': json.dumps({'type':'CloseStream'})},
        {'type':'websocket.disconnect'}
    ]
    sent = __import__('asyncio').get_event_loop().run_until_complete(_run(events))
    texts = [json.loads(t) for t in _texts(sent)]
    kinds = [t.get('type') for t in texts]
    assert 'Results' in kinds, "No Results emitted on CloseStream"
    assert 'UtteranceEnd' in kinds, "No UtteranceEnd emitted on CloseStream"
    # Check Deepgram-aligned shape: channel.is_final == True
    res = next((t for t in texts if t.get('type')=='Results'), None)
    assert isinstance(res, dict) and isinstance(res.get('channel'), dict), "Results missing channel"
    assert res['channel'].get('is_final') is True, "Results.channel.is_final must be true"
