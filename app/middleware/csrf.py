# app/middleware/csrf.py
import os, secrets
from flask import request, jsonify, session

CSRF_HEADER = "X-CSRF-Token"
CSRF_SESSION_KEY = "_csrf_token"

def _issue_token():
    tok = secrets.token_hex(16)
    session[CSRF_SESSION_KEY] = tok
    return tok

def csrf_before_request():
    m = (request.method or "GET").upper()
    if m in ("GET", "HEAD", "OPTIONS"):
        return
    sent = request.headers.get(CSRF_HEADER, "")
    want = session.get(CSRF_SESSION_KEY, "")
    if not sent or not want or sent != want:
        resp = jsonify({"ok": False, "error": "csrf_missing_or_invalid"})
        resp.status_code = 403
        return resp

def make_csrf_route(app):
    @app.get("/api/v1/csrf")
    def csrf_get():
        token = session.get(CSRF_SESSION_KEY) or _issue_token()
        resp = jsonify({"ok": True, "csrf": token})
        resp.headers[CSRF_HEADER] = token
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.route('/api/v1/csrf', methods=['HEAD'])
    def csrf_head():
        token = session.get(CSRF_SESSION_KEY) or _issue_token()
        return ("", 200, {CSRF_HEADER: token, "Cache-Control": "no-store"})

def ensure_csrf_headers(resp):
    try:
        token = session.get(CSRF_SESSION_KEY) or _issue_token()
        resp.headers[CSRF_HEADER] = token
        resp.headers["Cache-Control"] = "no-store"
    except Exception:
        pass
    return resp
