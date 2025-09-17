import base64, hmac, json, time, hashlib, os
SECRET = (os.environ.get("SECRET_KEY","dev-secret") or "dev-secret").encode("utf-8")
def issue(session_id: str, user: str, ttl_s: int = 300) -> str:
    payload = {"sid": session_id, "sub": user, "iat": int(time.time()), "exp": int(time.time()) + ttl_s}
    b = json.dumps(payload, separators=(",",":")).encode("utf-8")
    sig = hmac.new(SECRET, b, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(b).decode("ascii") + "." + base64.urlsafe_b64encode(sig).decode("ascii")
def verify(token: str) -> dict:
    if not token: raise ValueError("missing_token")
    b64_payload, b64_sig = token.split(".")
    b = base64.urlsafe_b64decode(b64_payload.encode("ascii"))
    sig = base64.urlsafe_b64decode(b64_sig.encode("ascii"))
    if not hmac.compare_digest(hmac.new(SECRET, b, hashlib.sha256).digest(), sig): raise ValueError("bad_sig")
    payload = json.loads(b.decode("utf-8"))
    if int(time.time()) > int(payload["exp"]): raise ValueError("expired")
    return payload
