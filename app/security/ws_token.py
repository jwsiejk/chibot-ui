# app/security/ws_token.py
import base64, hmac, json, time, hashlib, os, secrets
from typing import Dict, Any

SECRET = (os.environ.get("SECRET_KEY", "dev-secret") or "dev-secret").encode("utf-8")

# ---- Tunables (via env) ------------------------------------------------------

# Max allowed TTL for issued tokens (seconds). Default 300s (5 min).
_MAX_TTL = int(os.environ.get("WS_TOKEN_MAX_TTL_S", "300") or "300")

# Allowable clock skew when verifying iat/exp (seconds).
_SKEW_S = int(os.environ.get("WS_TOKEN_SKEW_S", "60") or "60")

# Hard cap on token length to avoid waste/attacks.
_MAX_TOKEN_LEN = int(os.environ.get("WS_TOKEN_MAX_LEN", "2048") or "2048")

# Simple in-process replay cache: jti -> exp (purged on verify). Per worker.
_REPLAY_CACHE: Dict[str, int] = {}


# ---- Tiny helpers ------------------------------------------------------------

def _b64url_encode(b: bytes) -> str:
    """URL-safe base64 without padding (RFC 4648 §5)."""
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64url_decode(s: str) -> bytes:
    """Decode URL-safe base64 with optional padding."""
    s = s.strip()
    # Add padding back if missing
    s = s + "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s.encode("ascii"))


def _purge_replay(now: int) -> None:
    stale = [k for k, exp in _REPLAY_CACHE.items() if exp <= now]
    for k in stale:
        _REPLAY_CACHE.pop(k, None)


def _now() -> int:
    return int(time.time())


def _require_fields(payload: Dict[str, Any]) -> None:
    # Minimal schema & types
    for k in ("sid", "sub", "iat", "exp"):
        if k not in payload:
            raise ValueError(f"missing_{k}")
    if not isinstance(payload["sid"], str) or not payload["sid"]:
        raise ValueError("bad_sid")
    if not isinstance(payload["sub"], str) or not payload["sub"]:
        raise ValueError("bad_sub")
    try:
        iat = int(payload["iat"])
        exp = int(payload["exp"])
    except Exception:
        raise ValueError("bad_times")
    if exp <= iat:
        raise ValueError("bad_window")


# ---- Public API --------------------------------------------------------------

def issue(session_id: str, user: str, ttl_s: int = 300) -> str:
    """
    Issue a short-lived WS token.
    - TTL is clamped to _MAX_TTL (default 300s).
    - Returns padding-less base64url pieces: <payload>.<sig>
      (safe to embed in WebSocket subprotocols).
    """
    try:
        max_ttl = max(1, _MAX_TTL)
    except Exception:
        max_ttl = 300

    ttl = ttl_s or 300
    ttl = min(ttl, max_ttl)

    now = _now()
    payload = {
        "sid": session_id,
        "sub": user,
        "iat": now,
        "exp": now + ttl,
        "jti": secrets.token_urlsafe(16),
    }

    b = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    sig = hmac.new(SECRET, b, hashlib.sha256).digest()

    # Padding-less to satisfy Sec-WebSocket-Protocol token grammar
    return _b64url_encode(b) + "." + _b64url_encode(sig)


def verify(token: str) -> Dict[str, Any]:
    """
    Verify a WS token; returns the payload dict on success.
    Accepts both padding-less and padded base64url tokens.
    Enforces:
      - HMAC signature
      - iat/exp with skew allowance
      - optional TTL clamp sanity
      - jti replay protection (per worker)
    """
    if not token:
        raise ValueError("missing_token")

    if len(token) > _MAX_TOKEN_LEN:
        raise ValueError("token_too_long")

    # Expect exactly one dot
    parts = token.split(".")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError("bad_format")

    b64_payload, b64_sig = parts[0], parts[1]

    try:
        b = _b64url_decode(b64_payload)
        sig = _b64url_decode(b64_sig)
    except Exception:
        raise ValueError("bad_base64")

    mac = hmac.new(SECRET, b, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, sig):
        raise ValueError("bad_sig")

    try:
        payload = json.loads(b.decode("utf-8"))
    except Exception:
        raise ValueError("bad_payload")

    # Basic schema checks
    _require_fields(payload)

    now = _now()
    iat = int(payload["iat"])
    exp = int(payload["exp"])

    # Skew-aware validity window
    if now < (iat - _SKEW_S):
        raise ValueError("not_yet_valid")
    if now > (exp + _SKEW_S):
        raise ValueError("expired")

    # Optional sanity: ensure window isn't wildly larger than configured max
    max_window = max(1, _MAX_TTL) + (2 * _SKEW_S)
    if (exp - iat) > max_window:
        raise ValueError("ttl_excessive")

    # Replay protection
    _purge_replay(now)
    jti = payload.get("jti")
    if jti:
        if jti in _REPLAY_CACHE:
            raise ValueError("replayed_token")
        _REPLAY_CACHE[jti] = exp

    return payload
