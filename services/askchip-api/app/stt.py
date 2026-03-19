from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


class SttError(RuntimeError):
    pass


@dataclass(frozen=True)
class SttResult:
    text: str
    language: str | None
    duration_seconds: float | None
    segments: list[dict[str, Any]]


class FasterWhisperSttService:
    def __init__(
        self,
        *,
        model_name: str,
        device: str,
        compute_type: str,
        cpu_threads: int,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.cpu_threads = cpu_threads

    def transcribe_bytes(self, audio_bytes: bytes, *, filename: str | None = None) -> SttResult:
        if not audio_bytes:
            raise SttError('No audio bytes were provided for transcription.')

        suffix = Path(filename or 'voice-turn.webm').suffix or '.webm'
        with NamedTemporaryFile(suffix=suffix, delete=True) as handle:
            handle.write(audio_bytes)
            handle.flush()
            model = self._load_model(
                self.model_name,
                self.device,
                self.compute_type,
                self.cpu_threads,
            )
            try:
                segments, info = model.transcribe(handle.name, vad_filter=False)
            except Exception as exc:  # pragma: no cover - exact backend exception varies by runtime
                raise SttError(f'faster-whisper transcription failed: {exc}') from exc

            collected_segments = []
            parts: list[str] = []
            duration_seconds = getattr(info, 'duration', None)
            language = getattr(info, 'language', None)
            for segment in segments:
                text = str(getattr(segment, 'text', '') or '')
                parts.append(text)
                collected_segments.append(
                    {
                        'start': getattr(segment, 'start', None),
                        'end': getattr(segment, 'end', None),
                        'text': text,
                    }
                )

        return SttResult(
            text=' '.join(part.strip() for part in parts if part.strip()).strip(),
            language=language,
            duration_seconds=float(duration_seconds) if isinstance(duration_seconds, (int, float)) else None,
            segments=collected_segments,
        )

    @staticmethod
    @lru_cache(maxsize=4)
    def _load_model(model_name: str, device: str, compute_type: str, cpu_threads: int):
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover - depends on optional runtime package install
            raise SttError(
                'faster-whisper is not installed or failed to import. '
                'Install AskChip API dependencies with the STT extras before using voice input.'
            ) from exc

        return WhisperModel(
            model_name,
            device=device,
            compute_type=compute_type,
            cpu_threads=cpu_threads,
        )
