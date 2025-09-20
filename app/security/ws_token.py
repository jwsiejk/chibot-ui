import base64, hmac, json, time, hashlib, os, secrets
SECRET = (os.environ.get("SECRET_KEY","dev-secret") or "dev-secret").encode("utf-8")

# Simple in-process replay cache: jti -> exp (purged on verify). Per-worker.
_REPLAY_CACHE = {}

def _purge_replay(now:int):
    stale = [k for k,exp in _REPLAY_CACHE.items() if exp <= now]
    for k in stale:
        _REPLAY_CACHE.pop(k, None)

def issue(session_id: str, user: str, ttl_s: int = 300) -> str:
    # Clamp TTL to max 5 minutes in production to reduce replay window
    try:
        max_ttl = int(os.environ.get("WS_TOKEN_MAX_TTL_S", "300"))
    except Exception:
        max_ttl = 300
    ttl_s = min(ttl_s or 300, max_ttl if max_ttl > 0 else 300)
    now = int(time.time())
    payload = {"sid": session_id, "sub": user, "iat": now, "exp": now + ttl_s, "jti": secrets.token_urlsafe(16)}
    b = json.dumps(payload, separators=(",",":")).encode("utf-8")
    sig = hmac.new(SECRET, b, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(b).decode("ascii") + "." + base64.urlsafe_b64encode(sig).decode("ascii")

def verify(token: str) -> dict:
    if not token: raise ValueError("missing_token")
    b64_payload, b64_sig = token.split(".")
    b = base64.urlsafe_b64decode(b64_payload.encode("ascii"))
    sig = base64.urlsafe_b64decode(b64_sig.encode("ascii"))
    if not hmac.compare_digest(hmac.new(SECRET, b, hashlib.sha256).digest(), sig):
        raise ValueError("bad_sig")
    payload = json.loads(b.decode("utf-8"))
    now = int(time.time())
    if now > int(payload["exp"]):
        raise ValueError("expired")
    # Replay protection: jti must be unseen; then mark used until exp
    jti = payload.get("jti")
    _purge_replay(now)
    if jti:
        if jti in _REPLAY_CACHE:
            raise ValueError("replayed_token")
        _REPLAY_CACHE[jti] = int(payload["exp"])
    return payload
