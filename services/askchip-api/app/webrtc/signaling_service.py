from __future__ import annotations

from app.webrtc.peer_factory import AiortcPeerFactory, PeerFactory
from app.webrtc.session_store import WebRtcSessionStore
from app.webrtc_models import SessionDescriptionModel, WebRtcSignalResponse


class WebRtcSignalingService:
    def __init__(self, store: WebRtcSessionStore | None = None, peer_factory: PeerFactory | None = None) -> None:
        self._store = store or WebRtcSessionStore()
        self._peer_factory = peer_factory or AiortcPeerFactory()

    async def negotiate_offer(self, *, session_id: str | None, offer: SessionDescriptionModel) -> WebRtcSignalResponse:
        await self._store.prune_expired_sessions()
        session = self._store.resolve_session(session_id)
        await self._store.update_offer(session.session_id, offer.sdp)
        result = await self._peer_factory.create_answer(offer)
        if result.status == 'answer_created' and result.peer is not None:
            await self._store.attach_peer(session.session_id, result.peer)
        else:
            await self._store.release(session.session_id)
        return WebRtcSignalResponse(
            session_id=session.session_id,
            event='answer',
            status=result.status,
            detail=result.detail,
            answer=result.answer,
        )

    async def disconnect(self, session_id: str | None) -> WebRtcSignalResponse:
        await self._store.prune_expired_sessions()
        if session_id:
            await self._store.release(session_id)
        return WebRtcSignalResponse(
            session_id=session_id or '',
            event='disconnected',
            status='disconnected',
            detail='WebRTC foundation session released.',
            answer=None,
        )

    async def clear(self) -> None:
        await self._store.clear()

    def get_session(self, session_id: str):
        return self._store.get(session_id)

    def session_count(self) -> int:
        return self._store.size()
