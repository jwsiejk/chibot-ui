from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.ollama import OllamaClient, OllamaUnavailableError
from app.tts import TtsAdapter, TtsError


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ReadinessCheckState:
    label: str
    status: str = 'pending'
    detail: str | None = None
    checked_at: str | None = None
    optional: bool = False

    def mark(self, status: str, detail: str | None = None) -> None:
        self.status = status
        self.detail = detail
        self.checked_at = utcnow_iso()


@dataclass
class ReadinessTracker:
    ollama: OllamaClient
    tts: TtsAdapter
    ollama_warmup_enabled: bool
    tts_warmup_enabled: bool
    checks: dict[str, ReadinessCheckState] = field(default_factory=dict)
    _warmup_task: asyncio.Task | None = None

    def __post_init__(self) -> None:
        self.checks = {
            'ollama': ReadinessCheckState(label='Ollama model', status='pending' if self.ollama_warmup_enabled else 'not_run', detail='Warm-up not started yet.' if self.ollama_warmup_enabled else 'Warm-up disabled by config.'),
            'tts': ReadinessCheckState(label='Kokoro speech', status='pending' if self.tts_warmup_enabled else 'not_run', detail='Warm-up not started yet.' if self.tts_warmup_enabled else 'Warm-up disabled by config.', optional=True),
        }

    def warmup_active(self) -> bool:
        return self._warmup_task is not None and not self._warmup_task.done()

    def start(self) -> None:
        if self._warmup_task is None:
            self._warmup_task = asyncio.create_task(self._run_warmups())

    async def _run_warmups(self) -> None:
        if self.ollama_warmup_enabled:
            await self._warm_ollama()
        if self.tts_warmup_enabled:
            await self._warm_tts()

    async def _warm_ollama(self) -> None:
        try:
            await self.ollama.warmup()
        except OllamaUnavailableError as exc:
            self.checks['ollama'].mark('failed', str(exc))
        else:
            self.checks['ollama'].mark('ready', f'Model {self.ollama.model} responded to warm-up.')

    async def _warm_tts(self) -> None:
        try:
            await asyncio.to_thread(self.tts.synthesize, 'Warm.')
        except TtsError as exc:
            self.checks['tts'].mark('failed', str(exc))
        else:
            self.checks['tts'].mark('ready', 'Kokoro synthesized a short local warm-up clip.')

    def snapshot(self) -> dict[str, object]:
        return {
            'local_only': True,
            'warmup_active': self.warmup_active(),
            'checks': {
                key: {
                    'label': value.label,
                    'status': value.status,
                    'detail': value.detail,
                    'checked_at': value.checked_at,
                    'optional': value.optional,
                }
                for key, value in self.checks.items()
            },
        }
