from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4


class ClosablePeer(Protocol):
    async def close(self) -> None: ...


@dataclass
class SignalingSession:
    session_id: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_offer_sdp: str | None = None
    peer: ClosablePeer | None = None


class WebRtcSessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SignalingSession] = {}

    def resolve_session(self, session_id: str | None) -> SignalingSession:
        resolved_id = session_id or str(uuid4())
        session = self._sessions.get(resolved_id)
        if session is None:
            session = SignalingSession(session_id=resolved_id)
            self._sessions[resolved_id] = session
        return session

    def get(self, session_id: str) -> SignalingSession | None:
        return self._sessions.get(session_id)

    async def attach_peer(self, session_id: str, peer: ClosablePeer | None) -> SignalingSession:
        session = self.resolve_session(session_id)
        if session.peer is not None and session.peer is not peer:
            await session.peer.close()
        session.peer = peer
        session.updated_at = datetime.now(timezone.utc).isoformat()
        return session

    async def update_offer(self, session_id: str, sdp: str) -> SignalingSession:
        session = self.resolve_session(session_id)
        session.last_offer_sdp = sdp
        session.updated_at = datetime.now(timezone.utc).isoformat()
        return session

    async def release(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session and session.peer is not None:
            await session.peer.close()

    async def clear(self) -> None:
        for session_id in list(self._sessions):
            await self.release(session_id)
