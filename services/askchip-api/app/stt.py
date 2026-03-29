from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import os
import tempfile
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

    def runtime_details(self) -> dict[str, object]:
        requested_device = self.device
        selected_device = requested_device
        requested_compute_type = self.compute_type
        resolved_compute_type = requested_compute_type
        warning: str | None = None

        if requested_device == 'auto':
            selected_device = 'cpu'
            try:
                import torch  # type: ignore

                if torch.cuda.is_available():
                    selected_device = 'cuda'
            except Exception:
                warning = 'torch CUDA availability probe failed while resolving auto STT device; defaulting to cpu.'
        if requested_compute_type == 'auto':
            resolved_compute_type = 'int8_float16' if selected_device == 'cuda' else 'int8'

        return {
            'stt_model': self.model_name,
            'requested_device': requested_device,
            'selected_device': selected_device,
            'requested_compute_type': requested_compute_type,
            'resolved_compute_type': resolved_compute_type,
            'cpu_threads': self.cpu_threads,
            **({'warning': warning} if warning else {}),
        }

    def transcribe_bytes(self, audio_bytes: bytes, *, filename: str | None = None) -> SttResult:
        if not audio_bytes:
            raise SttError('No audio bytes were provided for transcription.')

        suffix = Path(filename or 'voice-turn.webm').suffix or '.webm'
        fd, temp_path = tempfile.mkstemp(suffix=suffix)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(audio_bytes)

            runtime = self.runtime_details()
            model = self._load_model(
                self.model_name,
                str(runtime['selected_device']),
                str(runtime['resolved_compute_type']),
                self.cpu_threads,
            )
            try:
                segments, info = model.transcribe(temp_path, vad_filter=False)
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
        finally:
            Path(temp_path).unlink(missing_ok=True)

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
