"""Utilities for managing session-scoped streaming resources."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Dict, Optional

_logger = logging.getLogger(__name__)

Cleanup = Callable[[], Optional[Awaitable[None]]]


class _SessionStreams:
    """Track cleanup callbacks for a single session."""

    def __init__(self) -> None:
        self._input_finalizers: list[Cleanup] = []
        self._output_finalizer: Cleanup | None = None

    def add_input_finalizer(self, closer: Cleanup) -> None:
        if closer is not None:
            self._input_finalizers.append(closer)

    def set_output_finalizer(self, closer: Cleanup | None) -> None:
        self._output_finalizer = closer

    def is_empty(self) -> bool:
        return not self._input_finalizers and self._output_finalizer is None

    async def aclose(self) -> None:
        await self._run_closer(self._output_finalizer)
        self._output_finalizer = None
        while self._input_finalizers:
            closer = self._input_finalizers.pop()
            await self._run_closer(closer)

    @staticmethod
    async def _run_closer(closer: Cleanup | None) -> None:
        if closer is None:
            return
        try:
            result = closer()
        except Exception:  # pragma: no cover - defensive logging
            _logger.exception("Streaming finalizer raised during invocation")
            return
        if asyncio.iscoroutine(result) or isinstance(result, Awaitable):
            try:
                await result
            except Exception:  # pragma: no cover - defensive logging
                _logger.exception("Streaming finalizer coroutine failed")


class StreamingController:
    """Register and execute streaming cleanup hooks per session."""

    def __init__(self) -> None:
        self._sessions: Dict[str, _SessionStreams] = {}

    def register_input_finalizer(self, sid: str, closer: Cleanup) -> None:
        """Register a cleanup callback for inbound (ASR) streaming state."""

        if not sid or closer is None:
            return
        session = self._sessions.setdefault(sid, _SessionStreams())
        session.add_input_finalizer(closer)

    def set_output_finalizer(self, sid: str, closer: Cleanup | None) -> None:
        """Replace the cleanup callback for outbound (TTS) streaming state."""

        if not sid:
            return
        if closer is None:
            session = self._sessions.get(sid)
            if session is None:
                return
            session.set_output_finalizer(None)
            if session.is_empty():
                self._sessions.pop(sid, None)
            return

        session = self._sessions.setdefault(sid, _SessionStreams())
        session.set_output_finalizer(closer)

    def close_session(self, sid: str) -> None:
        """Run any registered cleanup callbacks for the session."""

        if not sid:
            return
        session = self._sessions.pop(sid, None)
        if session is None:
            return
        self._schedule(session.aclose())

    def reset_session(self, sid: str) -> None:
        """Clear state for the session, running cleanups if present."""

        self.close_session(sid)

    def _schedule(self, coro: Awaitable[None]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(coro)
            return

        task = loop.create_task(coro)
        task.add_done_callback(self._log_task_error)

    @staticmethod
    def _log_task_error(task: asyncio.Task[None]) -> None:
        try:
            task.result()
        except asyncio.CancelledError:  # pragma: no cover - expected when shutting down
            pass
        except Exception:  # pragma: no cover - defensive logging
            _logger.exception("Streaming cleanup task failed")


__all__ = ["StreamingController"]
