
try:
    from app.api_v1.admin import _emit
except Exception:
    def _emit(*a, **k): pass

from flask import request, jsonify, session
from itsdangerous import URLSafeSerializer
import os, secrets

CSRF_HEADER = "X-CSRF-Token"
CSRF_SESSION_KEY = "_csrf_token"

def _secret():
    return os.environ.get("SECRET_KEY") or "dev-secret-change-me"

def _issue_token():
    # Ensure a durable session id so Flask sets a session cookie
    session.setdefault("_sid", secrets.token_hex(16))
    s = URLSafeSerializer(_secret(), salt="askchip-csrf")
    token = s.dumps({"sid": session["_sid"]})
    session[CSRF_SESSION_KEY] = token
    return token

def csrf_before_request():
    """Guard: only enforce on unsafe methods; allow GET (incl. /api/v1/greet)."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        sent = request.headers.get(CSRF_HEADER)
        expected = session.get(CSRF_SESSION_KEY)
        if not sent or not expected or sent != expected:
            try:
                _emit('csrf', msg='fail', path=request.path)
            except Exception:
                pass
            return jsonify({"ok": False, "error": "csrf_failed"}), 403
    return None

def make_csrf_route(app):
    @app.get("/api/v1/auth/csrf")
    def get_csrf_route():
        token = _issue_token()
        resp = jsonify({"ok": True, "csrf": token})
        # Also mirror token in a header for convenience
        resp.headers[CSRF_HEADER] = token
        resp.headers["Cache-Control"] = "no-store"
        return resp

def ensure_csrf_headers(resp):
    """Helper to attach a token header on successful responses like greet."""
    try:
        token = session.get(CSRF_SESSION_KEY) or _issue_token()
        resp.headers[CSRF_HEADER] = token
        resp.headers["Cache-Control"] = "no-store"
    except Exception:
        pass
    return resp
