import logging
import pytest

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
