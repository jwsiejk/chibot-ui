import pytest

from app.services.asr.policies import to_sm_params


def test_defaults_when_policy_missing():
    params = to_sm_params(None)

    assert params["message"] == "StartRecognition"
    assert params["audio_format"] == {
        "type": "raw",
        "encoding": "pcm_s16le",
        "sample_rate": 16000,
        "channels": 1,
    }

    transcription = params["transcription_config"]
    assert transcription["language"] == "en"
    assert transcription["enable_partials"] is True
    assert "max_delay" not in transcription
    assert "additional_vocab" not in transcription


def test_speechmatics_policy_mapping():
    policy = {
        "policy": {
            "nlu": {"language": "  EN-GB  "},
            "asr": {
                "speechmatics": {
                    "language": "fr",
                    "enable_partials": False,
                    "punctuation": {
                        "enabled": True,
                        "overrides": ["question_mark", "", " exclamation_mark "],
                    },
                    "diarization": {"enabled": True},
                    "max_final_latency_ms": 100,
                    "profanity_filter": True,
                    "custom_vocab": [
                        "Alice",
                        {"content": "Bob", "sounds_like": ["Bawb", ""], "boost": "1.5"},
                        {"content": "   "},
                    ],
                }
            },
        }
    }

    params = to_sm_params(policy)
    transcription = params["transcription_config"]

    assert transcription["language"] == "fr"
    assert transcription["enable_partials"] is False
    assert transcription["diarization"] is True
    assert pytest.approx(transcription["max_delay"], rel=0, abs=1e-6) == 0.7
    assert transcription["profanity_filter"] is True
    assert transcription["enable_punctuation"] is True
    assert transcription["punctuation_overrides"] == {
        "permitted_marks": ["question_mark", "exclamation_mark"]
    }
    assert transcription["additional_vocab"] == [
        {"content": "Alice"},
        {"content": "Bob", "sounds_like": ["Bawb"], "boost": pytest.approx(1.5)},
    ]


def test_policy_fallbacks_and_clamping():
    policy = {
        "policy": {
            "nlu": {"language": " es "},
            "asr": {
                "enable_partials": False,
                "speechmatics": {
                    "max_final_latency_ms": 5000,
                    "custom_vocab": {"entries": [{"content": "Gamma"}]},
                    "profanity_filter": "not-bool",
                },
            },
        }
    }

    params = to_sm_params(policy)
    transcription = params["transcription_config"]

    assert transcription["language"] == "es"
    assert transcription["enable_partials"] is False
    assert pytest.approx(transcription["max_delay"], rel=0, abs=1e-6) == 4.0
    assert transcription["additional_vocab"] == [{"content": "Gamma"}]
    assert "profanity_filter" not in transcription

