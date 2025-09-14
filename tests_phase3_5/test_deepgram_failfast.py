
import os, pytest
from app.services.providers.streaming_asr.deepgram_client import DeepgramClient

def test_deepgram_failfast_without_key(monkeypatch):
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    with pytest.raises(RuntimeError) as ei:
        DeepgramClient({"deepgram": {}})
    assert "DEEPGRAM_API_KEY" in str(ei.value)
