from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class SessionDescriptionModel(BaseModel):
    sdp: str
    type: str


class WebRtcOfferRequest(BaseModel):
    session_id: str | None = None
    offer: SessionDescriptionModel


class WebRtcSignalEnvelope(BaseModel):
    event: Literal['offer', 'disconnect']
    session_id: str | None = None
    offer: SessionDescriptionModel | None = None


class WebRtcSignalResponse(BaseModel):
    session_id: str
    event: Literal['answer', 'disconnected', 'error']
    status: str
    detail: str
    answer: SessionDescriptionModel | None = None
