import os, json
import importlib

from app.ws.ws_asgi import ws_chat

def _run_ws(events):
    sent = []
    async def receive():
        return events.pop(0) if events else {'type':'websocket.disconnect'}
    async def send(msg):
        sent.append(msg)
    scope = {'type':'websocket','path':'/ws/v1/chat','query_string': b''}
    import asyncio
    asyncio.get_event_loop().run_until_complete(ws_chat(scope, receive, send))
    return sent

def test_phase3_config_values_flow_into_provider_url_and_config(monkeypatch):
    # Enable vendor path but keep DG in test-mode (no network)
    monkeypatch.setenv("DEEPGRAM_API_KEY","test")
    monkeypatch.setenv("DG_TEST_MODE","1")

    # Reload module to clear globals
    mod = importlib.import_module("app.services.streaming_asr.deepgram_client")
    importlib.reload(mod)

    # Send Configure first, then one binary slice to trigger connect, then CloseStream
    cfg = {
        "type":"Configure",
        "encoding":"opus",
        "sample_rate":16000,
        "channels":1,
        "smart_format":True,
        "punctuate":True,
        "vad_events":True,
        "utterance_end_ms":1500
    }
    events = [
        {'type':'websocket.receive','text': json.dumps(cfg)},
        {'type':'websocket.receive','bytes': b'\x00\x01'},
        {'type':'websocket.receive','text': json.dumps({'type':'CloseStream'})},
        {'type':'websocket.disconnect'},
    ]
    sent = _run_ws(events)

    # Assert DG client recorded URL and initial config
    assert mod.DG_LAST_URL is not None, "Deepgram URL not recorded"
    assert "sample_rate=16000" in mod.DG_LAST_URL, mod.DG_LAST_URL
    assert "channels=1" in mod.DG_LAST_URL, mod.DG_LAST_URL
    # Ensure flags appear (vad_events, smart_format, punctuate, utterance_end_ms)
    for tok in ("vad_events=true","smart_format=true","punctuate=true","utterance_end_ms=1500"):
        assert tok in mod.DG_LAST_URL, f"Missing {tok} in URL: {mod.DG_LAST_URL}"

    assert isinstance(mod.DG_LAST_CONFIG, dict) and mod.DG_LAST_CONFIG.get("sample_rate")==16000
    assert mod.DG_LAST_CONFIG.get("channels")==1
    assert mod.DG_LAST_CONFIG.get("vad_events") is True
    assert mod.DG_LAST_CONFIG.get("utterance_end_ms")==1500

    # Confirm server still emits final + UtteranceEnd on CloseStream
    texts = [json.loads(m['text']) for m in sent if m.get('type')=='websocket.send' and 'text' in m]
    kinds = [t.get('type') for t in texts]
    assert 'Results' in kinds and 'UtteranceEnd' in kinds
