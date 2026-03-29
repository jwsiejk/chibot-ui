from __future__ import annotations

import inspect
import io
import platform
import wave
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol


class TtsError(RuntimeError):
    pass


@dataclass(frozen=True)
class SynthesizedSpeech:
    audio_bytes: bytes
    content_type: str
    sample_rate_hz: int
    duration_ms: int | None
    metadata: dict[str, object]


class TtsAdapter(Protocol):
    def synthesize(self, text: str) -> SynthesizedSpeech: ...


@dataclass(frozen=True)
class KokoroConfig:
    voice: str
    model_path: str | None
    voices_path: str | None
    device: str
    sample_rate_hz: int = 24_000
    speed: float = 1.0
    lang_code: str = "a"


class KokoroTtsAdapter:
    def __init__(self, config: KokoroConfig) -> None:
        self.config = config

    def runtime_details(self) -> dict[str, object]:
        runtime = _kokoro_runtime_details(self.config)

        return {
            'tts_engine': 'kokoro-onnx',
            'requested_device': runtime['requested_device'],
            'selected_device': runtime['selected_device'],
            'provider': runtime['provider'],
            'available_providers': runtime['available_providers'],
            'voice': self.config.voice,
            'model_path': self.config.model_path,
            'voices_path': self.config.voices_path,
            'speed': self.config.speed,
            'lang_code': self.config.lang_code,
            **({'warning': runtime['warning']} if runtime.get('warning') else {}),
        }

    def synthesize(self, text: str) -> SynthesizedSpeech:
        normalized = text.strip()
        if not normalized:
            raise TtsError("Cannot synthesize empty assistant text.")

        try:
            samples, sample_rate_hz = _kokoro_runtime().create(
                normalized,
                voice=self.config.voice,
                speed=self.config.speed,
                lang=self.config.lang_code,
            )
        except Exception as exc:  # pragma: no cover - exercised through fake adapters in tests
            raise TtsError(str(exc)) from exc

        runtime = self.runtime_details()
        audio_bytes = pcm_f32_to_wav_bytes(samples, sample_rate_hz)
        duration_ms = (
            int((len(samples) / sample_rate_hz) * 1000)
            if sample_rate_hz and samples is not None
            else None
        )
        return SynthesizedSpeech(
            audio_bytes=audio_bytes,
            content_type="audio/wav",
            sample_rate_hz=sample_rate_hz,
            duration_ms=duration_ms,
            metadata={
                "engine": "kokoro",
                "voice": self.config.voice,
                "device": runtime['selected_device'],
                "provider": runtime['provider'],
                "sample_rate_hz": sample_rate_hz,
            },
        )


@lru_cache(maxsize=1)
def _kokoro_runtime():
    try:
        from kokoro_onnx import Kokoro  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on local runtime
        raise TtsError(
            "Kokoro is not installed. Install the backend extras with a Kokoro-compatible runtime before enabling assistant speech."
        ) from exc

    config = _kokoro_config_singleton
    runtime = _kokoro_runtime_details(config)
    _prepare_windows_onnxruntime_cuda(runtime)
    kwargs = {}
    if config.model_path is not None:
        kwargs["model_path"] = config.model_path
    if config.voices_path is not None:
        kwargs["voices_path"] = config.voices_path

    if _kokoro_supports_device_kwarg(Kokoro):
        kwargs["device"] = runtime['selected_device']
        return _init_kokoro_runtime(Kokoro, kwargs)

    return _init_kokoro_runtime(Kokoro, kwargs)


def _available_onnx_providers() -> list[str]:
    try:
        import onnxruntime as ort  # type: ignore

        return list(ort.get_available_providers())
    except Exception:
        return []


def _kokoro_runtime_details(config: KokoroConfig) -> dict[str, object]:
    providers = _available_onnx_providers()
    requested_device = config.device
    selected_device = requested_device if requested_device in {'cpu', 'cuda'} else 'auto'
    selected_provider = 'CPUExecutionProvider'
    warning: str | None = None

    if selected_device == 'auto':
        selected_device = 'cuda' if 'CUDAExecutionProvider' in providers else 'cpu'

    if selected_device == 'cuda':
        if not providers:
            selected_provider = 'unknown'
        elif 'CUDAExecutionProvider' in providers:
            selected_provider = 'CUDAExecutionProvider'
        else:
            selected_device = 'cpu'
            warning = 'ASKCHIP_TTS_DEVICE=cuda requested but CUDAExecutionProvider is unavailable; using CPUExecutionProvider.'

    return {
        'requested_device': requested_device,
        'selected_device': selected_device,
        'provider': selected_provider,
        'available_providers': providers,
        **({'warning': warning} if warning else {}),
    }


def _prepare_windows_onnxruntime_cuda(runtime_details: dict[str, object]) -> None:
    if platform.system() != 'Windows':
        return
    if runtime_details.get('provider') != 'CUDAExecutionProvider':
        return
    try:
        import onnxruntime as ort  # type: ignore

        preload = getattr(ort, 'preload_dlls', None)
        if callable(preload):
            preload()
    except Exception:
        return


def _kokoro_supports_device_kwarg(kokoro_cls) -> bool:
    try:
        signature = inspect.signature(kokoro_cls)
    except (TypeError, ValueError):
        return True

    return "device" in signature.parameters


def _init_kokoro_runtime(kokoro_cls, kwargs: dict[str, object]):
    try:
        return kokoro_cls(**kwargs)
    except TypeError as exc:
        if "device" not in kwargs or not _is_unexpected_device_kwarg_error(exc):
            raise TtsError(str(exc)) from exc

    fallback_kwargs = {key: value for key, value in kwargs.items() if key != "device"}
    try:
        return kokoro_cls(**fallback_kwargs)
    except Exception as exc:
        raise TtsError(str(exc)) from exc


def _is_unexpected_device_kwarg_error(exc: TypeError) -> bool:
    message = str(exc)
    return "device" in message and "unexpected keyword argument" in message


_kokoro_config_singleton = KokoroConfig(
    voice="af_heart", model_path=None, voices_path=None, device="cpu"
)


def configure_kokoro_runtime(config: KokoroConfig) -> None:
    global _kokoro_config_singleton
    _kokoro_config_singleton = config
    _kokoro_runtime.cache_clear()


def pcm_f32_to_wav_bytes(samples, sample_rate_hz: int) -> bytes:
    if sample_rate_hz <= 0:
        raise TtsError("Kokoro returned an invalid sample rate.")

    pcm16 = bytearray()
    for sample in samples:
        clamped = max(-1.0, min(1.0, float(sample)))
        pcm16.extend(
            int(clamped * 32767.0).to_bytes(2, byteorder="little", signed=True)
        )

    with io.BytesIO() as buffer:
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate_hz)
            wav_file.writeframes(bytes(pcm16))
        return buffer.getvalue()
