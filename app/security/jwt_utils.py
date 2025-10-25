"""JWT helper utilities for WebSocket authentication."""

import logging
import os
import time
import uuid

import jwt

_log = logging.getLogger("app.security.jwt")
_SECRET = os.environ["SECRET_KEY"]
_ALG = "HS256"
_AUD = "chat.v2"
_DEFAULT_TTL = 60


def mint_ws_token(user_id: str, sid: str, is_admin: bool, ttl_s: int = _DEFAULT_TTL) -> str:
    """Mint a short-lived WebSocket JWT for the given session."""
    issued_at = int(time.time())
    claims = {
        "sub": user_id,
        "sid": sid,
        "aud": _AUD,
        "iat": issued_at,
        "exp": issued_at + ttl_s,
        "jti": str(uuid.uuid4()),
        "is_admin": is_admin,
    }
    _log.info("evt=ws_jwt_mint sid=%s ttl=%s", sid, ttl_s)
    return jwt.encode(claims, _SECRET, algorithm=_ALG)


def verify_ws_token(token: str) -> dict:
    """Verify a WebSocket JWT and return its claims."""
    try:
        return jwt.decode(token, _SECRET, algorithms=[_ALG], audience=_AUD)
    except jwt.PyJWTError as err:  # pragma: no cover - passthrough logging
        _log.warning("evt=ws_jwt_verify_failed reason=%s", err.__class__.__name__)
        raise
