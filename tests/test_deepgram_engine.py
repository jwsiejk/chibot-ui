import logging
import sys
import types

import pytest

if "google" not in sys.modules:
    google_mod = types.ModuleType("google")
    api_core = types.ModuleType("google.api_core")
    api_core.exceptions = types.SimpleNamespace(OutOfRange=Exception)
    cloud_mod = types.ModuleType("google.cloud")

    class _DummySpeechClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

    class _DummyRecognitionConfig:
        class AudioEncoding:
            LINEAR16 = 1

        def __init__(self, *args, **kwargs) -> None:
            pass

    class _DummyStreamingRecognitionConfig:
        def __init__(self, *args, **kwargs) -> None:
            pass

    class _DummyStreamingRecognizeRequest:
        def __init__(self, *args, **kwargs) -> None:
            pass

    speech_mod = types.ModuleType("google.cloud.speech")
    speech_mod.SpeechClient = _DummySpeechClient
    speech_mod.RecognitionConfig = _DummyRecognitionConfig
    speech_mod.StreamingRecognitionConfig = _DummyStreamingRecognitionConfig
    speech_mod.StreamingRecognizeRequest = _DummyStreamingRecognizeRequest
    speech_mod.RecognitionAudio = object

    sys.modules["google"] = google_mod
    sys.modules["google.api_core"] = api_core
    sys.modules["google.api_core.exceptions"] = api_core.exceptions
    sys.modules["google.cloud"] = cloud_mod
    sys.modules["google.cloud.speech"] = speech_mod

from app.services.asr.deepgram_engine import DeepgramStreamingASREngine


def test_deepgram_partial_and_final_mapping(caplog: pytest.LogCaptureFixture) -> None:
    engine = DeepgramStreamingASREngine()
    engine._sid = "sid-deepgram"
    results: list[tuple[str, bool]] = []

    def _on_result(transcript: str, is_final: bool, event) -> None:
        results.append((transcript, is_final))

    engine._on_result = _on_result

    caplog.set_level(logging.INFO)
    engine._handle_message(
        {
            "type": "Results",
            "is_final": False,
            "channel": {"alternatives": [{"transcript": "hello"}]},
        }
    )
    engine._handle_message(
        {
            "type": "Results",
            "is_final": True,
            "channel": {"alternatives": [{"transcript": "hello world"}]},
        }
    )

    assert results == [("hello", False), ("hello world", True)]
    assert any("evt=asr_partial vendor=deepgram" in rec.message for rec in caplog.records)
    assert any("evt=asr_final vendor=deepgram" in rec.message for rec in caplog.records)


def test_deepgram_duplicate_final_dropped(caplog: pytest.LogCaptureFixture) -> None:
    engine = DeepgramStreamingASREngine()
    engine._sid = "sid-deepgram-dup"
    results: list[tuple[str, bool]] = []

    def _on_result(transcript: str, is_final: bool, event) -> None:
        results.append((transcript, is_final))

    engine._on_result = _on_result

    caplog.set_level(logging.INFO)
    engine._handle_message(
        {
            "type": "Results",
            "is_final": True,
            "channel": {"alternatives": [{"transcript": "final one"}]},
        }
    )
    engine._handle_message(
        {
            "type": "Results",
            "is_final": True,
            "channel": {"alternatives": [{"transcript": "final two"}]},
        }
    )

    assert results == [("final one", True)]
    assert any(
        "evt=asr_final_duplicate vendor=deepgram" in rec.message
        for rec in caplog.records
    )
