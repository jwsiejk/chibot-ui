from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.webrtc_models import SessionDescriptionModel


class ManagedPeer(Protocol):
    async def close(self) -> None: ...


@dataclass
class PeerFactoryResult:
    status: str
    detail: str
    answer: SessionDescriptionModel | None = None
    peer: ManagedPeer | None = None


class PeerFactory(Protocol):
    async def create_answer(self, offer: SessionDescriptionModel) -> PeerFactoryResult: ...


class UnsupportedPeerFactory:
    async def create_answer(self, offer: SessionDescriptionModel) -> PeerFactoryResult:
        return PeerFactoryResult(
            status='unsupported',
            detail='Server-side WebRTC media termination is unavailable because aiortc is not installed in this runtime.',
        )


class AiortcPeerAdapter:
    def __init__(self, peer) -> None:
        self._peer = peer

    async def close(self) -> None:
        await self._peer.close()


class AiortcPeerFactory:
    async def create_answer(self, offer: SessionDescriptionModel) -> PeerFactoryResult:
        try:
            from aiortc import RTCPeerConnection, RTCSessionDescription
        except ImportError:
            return await UnsupportedPeerFactory().create_answer(offer)

        peer = RTCPeerConnection()
        try:
            await peer.setRemoteDescription(RTCSessionDescription(sdp=offer.sdp, type=offer.type))
            answer = await peer.createAnswer()
            await peer.setLocalDescription(answer)
            local = peer.localDescription
            if local is None or not local.sdp:
                await peer.close()
                return PeerFactoryResult(
                    status='error',
                    detail='WebRTC answer generation completed without a local description.',
                )
            return PeerFactoryResult(
                status='answer_created',
                detail='Server-side WebRTC foundation answer created.',
                answer=SessionDescriptionModel(sdp=local.sdp, type=local.type),
                peer=AiortcPeerAdapter(peer),
            )
        except Exception as exc:
            await peer.close()
            return PeerFactoryResult(status='error', detail=f'WebRTC negotiation failed: {exc}')
