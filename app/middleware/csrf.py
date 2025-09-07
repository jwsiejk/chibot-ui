# app/middleware/csrf.py
from flask import request, jsonify
from urllib.parse import urlparse
from ..db import db
from ..security_state import get_csrf

CSRF_HEADER = "X-CSRF-Token"
CSRF_COOKIE = "csrf"
EXEMPT = {"/api/v1/auth/csrf", "/api/v1/auth/login", "/api/v1/auth/logout"}

# Kept for compatibility with existing callers
ALLOW_SAME_ORIGIN_JSON = True
def _same_origin_ok(req):
    try:
        ori = req.headers.get("Origin", "")
        host = req.host_url.rstrip("/")
        return bool(ori) and urlparse(ori).netloc == urlparse(host).netloc
    except Exception:
        return False

def csrf_before_request():
    """Enforce double-submit CSRF when enabled by config."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if request.path in EXEMPT:
            return None
        if not db.get_config().get("csrf_enforced", False):
            return None

        hdr = request.headers.get(CSRF_HEADER) or ""
        cok = request.cookies.get(CSRF_COOKIE) or ""
        tok = get_csrf() or ""

        # Require both header + cookie and that they match the server token
        if not hdr or not cok or hdr != cok or hdr != tok:
            return jsonify({"ok": False, "error": "csrf_failed"}), 403
    return None

def make_csrf_route(app):
    """Expose GET /api/v1/auth/csrf: returns JSON + sets httpOnly cookie."""
    @app.get("/api/v1/auth/csrf")
    def get_csrf_route():
        token = get_csrf()
        resp = jsonify({"ok": True, "csrf": token})
        # Path=/ so /api/v1/chat receives it; httpOnly; SameSite=Lax; Secure on HTTPS
        resp.set_cookie(
            CSRF_COOKIE, token,
            httponly=True, samesite="Lax", secure=True, path="/"
        )
        return resp
