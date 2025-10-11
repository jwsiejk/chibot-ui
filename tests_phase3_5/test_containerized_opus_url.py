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
    assert "endpointing=" not in url
    assert "interim_results=true" in url
    assert "utterance_end_ms=3000" in url
    assert "_containerized_forced" not in overrides["_transport"]


def test_containerized_interim_false_drops_utterance_end(monkeypatch):
    monkeypatch.setenv("DG_TEST_MODE", "1")
    overrides = {
        "_transport": {"containerized_opus": True},
        "interim_results": False,
    }
    url = _dg_url(overrides)
    assert "interim_results=false" in url
    assert "utterance_end_ms=" not in url
    assert "sample_rate=" not in url
    assert "channels=" not in url
    assert "_containerized_forced" not in overrides["_transport"]


def test_requested_raw_is_forced_to_containerized(monkeypatch):
    monkeypatch.setenv("DG_TEST_MODE", "1")
    overrides = {"_transport": {"containerized_opus": False}}
    url = _dg_url(overrides)
    assert "sample_rate=" not in url
    assert "channels=" not in url
    assert "encoding=" not in url
    assert overrides["_transport"]["containerized_opus"] is True
    assert overrides["_transport"].get("_containerized_forced") is True


def test_containerized_strips_explicit_audio_params(monkeypatch):
    monkeypatch.setenv("DG_TEST_MODE", "1")
    overrides = {
        "encoding": "linear16",
        "sample_rate": 16000,
        "channels": 2,
        "_transport": {"containerized_opus": True},
    }
    url = _dg_url(overrides)
    assert "encoding=" not in url
    assert "sample_rate=" not in url
    assert "channels=" not in url
    assert overrides["_transport"].get("_attempted_audio_params") == [
        "channels",
        "encoding",
        "sample_rate",
    ]
    assert "_dropped_audio_params" not in overrides["_transport"]


def test_containerized_ignores_env_raw_overrides(monkeypatch):
    monkeypatch.setenv("DG_TEST_MODE", "1")
    monkeypatch.setenv("DG_RAW_ENCODING", "linear16")
    monkeypatch.setenv("DG_RAW_SAMPLE_RATE", "16000")
    monkeypatch.setenv("DG_RAW_CHANNELS", "2")
    overrides = {"_transport": {"containerized_opus": True}}
    url = _dg_url(overrides)
    assert "encoding=" not in url
    assert "sample_rate=" not in url
    assert "channels=" not in url
    assert overrides["_transport"].get("_containerized_forced") is None

    # Clean up for future tests
    monkeypatch.delenv("DG_RAW_ENCODING", raising=False)
    monkeypatch.delenv("DG_RAW_SAMPLE_RATE", raising=False)
    monkeypatch.delenv("DG_RAW_CHANNELS", raising=False)
