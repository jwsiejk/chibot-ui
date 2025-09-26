import os
from app.services.streaming_asr.deepgram_client import _dg_url

def test_non_containerized_includes_encoding_sr_channels(monkeypatch):
    monkeypatch.setenv("DG_TEST_MODE", "1")
    url = _dg_url({"_transport": {"containerized_opus": False}})
    assert "encoding=opus" in url
    assert "sample_rate=48000" in url
    assert "channels=1" in url
