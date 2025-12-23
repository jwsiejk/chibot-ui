"""Shared ASR engine interfaces."""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Mapping, Optional

ResultCallback = Callable[
    [str, bool, Optional[Mapping[str, Any]]], Optional[Awaitable[None]]
]


class ASREngine:
    """Abstract interface for ASR engines."""

    async def open(
        self,
        *,
        sample_rate: int,
        language: str,
        sid: str,
        on_result: ResultCallback,
    ) -> None:
        raise NotImplementedError

    async def write(self, pcm: bytes) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError


__all__ = ["ASREngine", "ResultCallback"]
