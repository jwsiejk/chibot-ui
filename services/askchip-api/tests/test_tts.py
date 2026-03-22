from __future__ import annotations

import sys
import types

import pytest

from app.tts import KokoroConfig, KokoroTtsAdapter, TtsError, configure_kokoro_runtime


def test_kokoro_runtime_supports_device_kwarg(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeKokoro:
        def __init__(self, *, model_path=None, voices_path=None, device=None) -> None:
            calls.append(
                {"model_path": model_path, "voices_path": voices_path, "device": device}
            )

        def create(self, text: str, *, voice: str, speed: float, lang: str):
            assert text == "Hello runtime"
            assert voice == "af_heart"
            assert speed == 1.0
            assert lang == "a"
            return [0.0, 0.25, -0.25], 24_000

    monkeypatch.setitem(
        sys.modules, "kokoro_onnx", types.SimpleNamespace(Kokoro=FakeKokoro)
    )
    configure_kokoro_runtime(
        KokoroConfig(
            voice="af_heart",
            model_path="model.onnx",
            voices_path="voices.bin",
            device="cuda",
        )
    )

    speech = KokoroTtsAdapter(
        KokoroConfig(
            voice="af_heart",
            model_path="model.onnx",
            voices_path="voices.bin",
            device="cuda",
        )
    ).synthesize("Hello runtime")

    assert calls == [
        {"model_path": "model.onnx", "voices_path": "voices.bin", "device": "cuda"}
    ]
    assert speech.audio_bytes.startswith(b"RIFF")
    assert speech.sample_rate_hz == 24_000
    assert speech.metadata["device"] == "cuda"


def test_kokoro_runtime_without_device_kwarg_still_synthesizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    class FakeKokoro:
        def __init__(self, *, model_path=None, voices_path=None) -> None:
            calls.append({"model_path": model_path, "voices_path": voices_path})

        def create(self, text: str, *, voice: str, speed: float, lang: str):
            assert text == "Hello fallback"
            assert voice == "af_heart"
            assert speed == 1.0
            assert lang == "a"
            return [0.1, -0.1], 24_000

    monkeypatch.setitem(
        sys.modules, "kokoro_onnx", types.SimpleNamespace(Kokoro=FakeKokoro)
    )
    configure_kokoro_runtime(
        KokoroConfig(
            voice="af_heart",
            model_path="model.onnx",
            voices_path="voices.bin",
            device="cpu",
        )
    )

    speech = KokoroTtsAdapter(
        KokoroConfig(
            voice="af_heart",
            model_path="model.onnx",
            voices_path="voices.bin",
            device="cpu",
        )
    ).synthesize("Hello fallback")

    assert calls == [{"model_path": "model.onnx", "voices_path": "voices.bin"}]
    assert speech.audio_bytes.startswith(b"RIFF")
    assert speech.sample_rate_hz == 24_000
    assert speech.metadata["device"] == "cpu"


def test_kokoro_runtime_init_failure_still_raises_tts_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeKokoro:
        def __init__(self, *, model_path=None, voices_path=None, device=None) -> None:
            raise RuntimeError("broken runtime")

    monkeypatch.setitem(
        sys.modules, "kokoro_onnx", types.SimpleNamespace(Kokoro=FakeKokoro)
    )
    configure_kokoro_runtime(
        KokoroConfig(voice="af_heart", model_path=None, voices_path=None, device="cpu")
    )

    with pytest.raises(TtsError, match="broken runtime"):
        KokoroTtsAdapter(
            KokoroConfig(
                voice="af_heart", model_path=None, voices_path=None, device="cpu"
            )
        ).synthesize("Hello error")
