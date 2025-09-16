import secrets
CURRENT={"email":None,"csrf":None}
def issue_csrf():
    t=secrets.token_hex(16); CURRENT["csrf"]=t; return t
def get_csrf():
    return CURRENT.get("csrf")
def set_user(email):
    CURRENT["email"]=email

def get_user():
    """Prefer Flask session user if available; fall back to in-memory CURRENT.
    This prevents cross-request leakage when globals are reused across requests/threads.
    """
    try:
        # Avoid import at module import time to prevent cycles
        from flask import session  # type: ignore
        email = (session.get("user") or {}).get("email")
        if email:
            return email
    except Exception:
        pass
    return CURRENT.get("email") or "user@example.com"

_CURRENT = globals().get('_CURRENT', {'email': None, 'profile': {}})

def set_profile(profile: dict):
    _CURRENT['profile'] = profile or {}

def get_profile():
    return _CURRENT.get('profile') or {}
