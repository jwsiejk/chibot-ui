from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from app.webrtc_models import SessionDescriptionModel, WebRtcOfferResponse


@dataclass
class SignalingSession:
    session_id: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_offer_sdp: str | None = None


class WebRtcSignalingService:
    def __init__(self) -> None:
        self._sessions: dict[str, SignalingSession] = {}

    def negotiate_offer(self, *, session_id: str | None, offer: SessionDescriptionModel) -> WebRtcOfferResponse:
        resolved_id = session_id or str(uuid4())
        signaling_session = self._sessions.get(resolved_id) or SignalingSession(session_id=resolved_id)
        signaling_session.last_offer_sdp = offer.sdp
        self._sessions[resolved_id] = signaling_session
        return WebRtcOfferResponse(
            session_id=resolved_id,
            status='unsupported',
            detail='Backend signaling is available, but server-side WebRTC media termination is not configured in this environment yet.',
            answer=None,
        )

    def get_session(self, session_id: str) -> SignalingSession | None:
        return self._sessions.get(session_id)
