
import os, sys, asyncio, json, types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 1) Schema shape check
from app.ws.schema_v1 import make_results

def test_make_results_shape():
    r = make_results(3, "hello")
    assert r["type"] == "Results"
    assert r["turn_id"] == 3
    assert "is_final" in r
    assert isinstance(r["channel"], dict)
    alts = r["channel"].get("alternatives")
    assert isinstance(alts, list) and len(alts) >= 1
    assert "transcript" in alts[0]

# 2) WS turn_id increments with CloseStream (mock ASR mode)
import importlib

async def drive_ws_chat(messages):
    # messages: list of inbound events to ws_chat (websocket.receive side)
    sent = []
    async def fake_receive():
        if not messages:
            # simulate disconnect
            return {"type": "websocket.disconnect"}
        m = messages.pop(0)
        return m

    async def fake_send(evt):
        sent.append(evt)

    scope = {"type":"websocket", "path": "/ws/v1/chat", "query_string": b"session_id=test"}
    from app.ws.ws_asgi import ws_chat
    await ws_chat(scope, fake_receive, fake_send)
    return sent

def _decode_ws_text(msg):
    assert msg["type"] == "websocket.send"
    data = json.loads(msg["text"])
    return data

def test_turn_id_and_results_flow_event_loop():
    os.environ["ASR_MOCK"] = "1"
    # Construct a fake session: accept -> binary frame -> CloseStream -> disconnect
    messages = [
        {"type":"websocket.receive", "bytes": b"\x00\x01\x02"},            # audio
        {"type":"websocket.receive", "text": json.dumps({"type":"CloseStream"})},
        {"type":"websocket.disconnect"},
    ]

    loop = asyncio.new_event_loop()
    try:
        sent = loop.run_until_complete(drive_ws_chat(messages))
    finally:
        loop.close()

    # Expect at least a Results and UtteranceEnd before close
    texts = [m for m in sent if m.get("type") == "websocket.send" and "text" in m]
    payloads = [_decode_ws_text(m) for m in texts]

    assert any(p.get("type") == "Results" for p in payloads), f"No Results in {payloads}"
    assert any(p.get("type") == "UtteranceEnd" for p in payloads), f"No UtteranceEnd in {payloads}"

    # Find matching turn_id across both
    r = next(p for p in payloads if p.get("type") == "Results")
    u = next(p for p in payloads if p.get("type") == "UtteranceEnd")
    assert r["turn_id"] == u["turn_id"] == 1
    assert "channel" in r and "alternatives" in r["channel"]
    assert "is_final" in r and r["is_final"] is True

# 3) Route-linter should pass
def test_route_linter_passes():
    import runpy
    code = 0
    try:
        runpy.run_path(str(ROOT/"scripts/route_linter.py"), run_name="__main__")
    except SystemExit as e:
        code = int(getattr(e, "code", 0) or 0)
    assert code == 0, "route-linter reported forbidden routes"
