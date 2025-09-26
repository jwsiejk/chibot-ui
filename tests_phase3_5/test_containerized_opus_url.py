import os
from app.services.streaming_asr.deepgram_client import DG_TEST_MODE
from app.services.streaming_asr.deepgram_client import _dg_url  # if exported
import pytest

def test_containerized_omits_sr_channels(monkeypatch):
    monkeypatch.setenv("DG_TEST_MODE", "1")
    # Build overrides with containerized flag in _transport
    overrides = {"_transport": {"containerized_opus": True}}
    url = _dg_url(overrides)
    assert "sample_rate=" not in url
    assert "channels=" not in url
    assert "encoding=" not in url

def test_non_containerized_includes_sr_channels(monkeypatch):
    monkeypatch.setenv("DG_TEST_MODE", "1")
    overrides = {"_transport": {"containerized_opus": False}}
    url = _dg_url(overrides)
    assert "sample_rate=48000" in url
    assert "channels=1" in url
