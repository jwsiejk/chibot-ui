from __future__ import annotations

from pydantic import BaseModel


class SessionDescriptionModel(BaseModel):
    sdp: str
    type: str


class WebRtcOfferRequest(BaseModel):
    session_id: str | None = None
    offer: SessionDescriptionModel


class WebRtcOfferResponse(BaseModel):
    session_id: str
    status: str
    detail: str
    answer: SessionDescriptionModel | None = None
