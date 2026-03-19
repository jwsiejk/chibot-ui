from __future__ import annotations

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from app.webrtc.signaling_service import WebRtcSignalingService
from app.webrtc_models import WebRtcSignalEnvelope, WebRtcSignalResponse


class WebRtcWebSocketHandler:
    def __init__(self, signaling_service: WebRtcSignalingService) -> None:
        self._signaling_service = signaling_service

    async def handle(self, websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                payload = WebRtcSignalEnvelope.model_validate(await websocket.receive_json())
                if payload.event == 'offer':
                    if payload.offer is None:
                        response = WebRtcSignalResponse(
                            session_id=payload.session_id or '',
                            event='error',
                            status='error',
                            detail='Offer payload is required for WebRTC negotiation.',
                            answer=None,
                        )
                    else:
                        response = await self._signaling_service.negotiate_offer(
                            session_id=payload.session_id,
                            offer=payload.offer,
                        )
                elif payload.event == 'disconnect':
                    response = await self._signaling_service.disconnect(payload.session_id)
                else:
                    response = WebRtcSignalResponse(
                        session_id=payload.session_id or '',
                        event='error',
                        status='error',
                        detail=f'Unsupported signaling event: {payload.event}',
                        answer=None,
                    )
                await websocket.send_json(response.model_dump())
        except WebSocketDisconnect:
            pass
