from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Protocol, cast
from uuid import uuid4


class ClosablePeer(Protocol):
    async def close(self) -> None: ...


class TerminalPeer(Protocol):
    def set_terminal_state_callback(self, callback: Callable[[str], Awaitable[None]]) -> None: ...


@dataclass
class SignalingSession:
    session_id: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_offer_sdp: str | None = None
    peer: ClosablePeer | None = None
    established: bool = False


class WebRtcSessionStore:
    def __init__(self, *, pending_session_timeout_seconds: int = 30) -> None:
        self._sessions: dict[str, SignalingSession] = {}
        self._pending_session_timeout = timedelta(seconds=pending_session_timeout_seconds)

    def resolve_session(self, session_id: str | None) -> SignalingSession:
        resolved_id = session_id or str(uuid4())
        session = self._sessions.get(resolved_id)
        if session is None:
            session = SignalingSession(session_id=resolved_id)
            self._sessions[resolved_id] = session
        return session

    def get(self, session_id: str) -> SignalingSession | None:
        return self._sessions.get(session_id)

    def size(self) -> int:
        return len(self._sessions)

    async def attach_peer(self, session_id: str, peer: ClosablePeer | None) -> SignalingSession:
        session = self.resolve_session(session_id)
        if session.peer is not None and session.peer is not peer:
            await session.peer.close()
        session.peer = peer
        session.established = peer is not None
        session.updated_at = datetime.now(timezone.utc).isoformat()
        if peer is not None and hasattr(peer, 'set_terminal_state_callback'):
            terminal_peer = cast(TerminalPeer, peer)
            terminal_peer.set_terminal_state_callback(lambda state: self.release_if_terminal(session_id, state))
        return session

    async def update_offer(self, session_id: str, sdp: str) -> SignalingSession:
        session = self.resolve_session(session_id)
        session.last_offer_sdp = sdp
        session.updated_at = datetime.now(timezone.utc).isoformat()
        return session

    async def release_if_terminal(self, session_id: str, state: str) -> None:
        if state in {'failed', 'closed'}:
            await self.release(session_id)

    async def prune_expired_pending_sessions(self, now: datetime | None = None) -> None:
        reference = now or datetime.now(timezone.utc)
        expired_ids = []
        for session_id, session in self._sessions.items():
            updated_at = datetime.fromisoformat(session.updated_at)
            if session.established or session.peer is not None:
                continue
            if reference - updated_at >= self._pending_session_timeout:
                expired_ids.append(session_id)
        for session_id in expired_ids:
            await self.release(session_id)

    async def release(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session and session.peer is not None:
            await session.peer.close()

    async def clear(self) -> None:
        for session_id in list(self._sessions):
            await self.release(session_id)
