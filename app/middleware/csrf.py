# app/middleware/csrf.py
from flask import request, jsonify
from urllib.parse import urlparse
from ..db import db
from ..security_state import get_csrf

CSRF_HEADER = "X-CSRF-Token"
CSRF_COOKIE = "csrf"
EXEMPT = {"/api/v1/auth/csrf", "/api/v1/auth/login", "/api/v1/auth/logout"}

# --- kept for compatibility with your file ---
ALLOW_SAME_ORIGIN_JSON = True
def _same_origin_ok(req):
    try:
        ori = req.headers.get("Origin", "")
        host = req.host_url.rstrip("/")
        return bool(ori) and urlparse(ori).netloc == urlparse(host).netloc
    except Exception:
        return False
# --------------------------------------------

def csrf_before_request():
    """Enforce double-submit when enabled in config."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if request.path in EXEMPT:
            return None
        if not db.get_config().get("csrf_enforced", False):
            return None

        hdr = request.headers.get(CSRF_HEADER) or ""
        cok = request.cookies.get(CSRF_COOKIE) or ""
        # Require header AND cookie and that they match the server token.
        if not hdr or not cok or hdr != cok or hdr != get_csrf():
            return jsonify({"ok": False, "error": "csrf_failed"}), 403
    return None

def make_csrf_route(app):
    """Expose GET /api/v1/auth/csrf: returns JSON + sets httpOnly cookie.
       'secure' is computed from request scheme / proxy headers; env can force.
    """
    @app.get("/api/v1/auth/csrf")
    def get_csrf_route():
        token = get_csrf()
        resp = jsonify({"ok": True, "csrf": token})
        try:
            xf_proto = (request.headers.get("X-Forwarded-Proto") or "").lower()
            is_https = bool(getattr(request, "is_secure", False)) or xf_proto == "https"
        except Exception:
            is_https = False
        import os as _os
        if _os.environ.get("FORCE_SECURE_COOKIES", "").lower() in ("1","true","yes","on"):
            is_https = True
        resp.set_cookie(
            CSRF_COOKIE, token,
            httponly=True, samesite="Lax", secure=bool(is_https), path="/"
        )
        return resp
