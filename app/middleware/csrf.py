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
    sent = request.headers.get(CSRF_HEADER, "") or request.headers.get("X-CSRFToken", "")
    want = session.get(CSRF_SESSION_KEY, "")
    cookie_tok = request.cookies.get("XSRF-TOKEN", "")
    if not want or not (sent == want or cookie_tok == want):
        resp = jsonify({"ok": False, "error": "csrf_missing_or_invalid"})
        resp.status_code = 403
        return resp

def make_csrf_route(app):
    @app.get("/api/v1/csrf")
    def csrf_get():
        token = session.get(CSRF_SESSION_KEY) or _issue_token()
        resp = jsonify({"ok": True, "csrf": token})
        resp.headers[CSRF_HEADER] = token
        try:
            secure = bool((request.environ.get('wsgi.url_scheme') == 'https') or (request.headers.get('X-Forwarded-Proto','').lower() == 'https'))
        except Exception:
            secure = False
        resp.set_cookie('XSRF-TOKEN', token, samesite='Lax', secure=secure, httponly=False, path='/')
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
        try:
            secure = bool((request.environ.get('wsgi.url_scheme') == 'https') or (request.headers.get('X-Forwarded-Proto','').lower() == 'https'))
        except Exception:
            secure = False
        resp.set_cookie('XSRF-TOKEN', token, samesite='Lax', secure=secure, httponly=False, path='/')
        resp.headers["Cache-Control"] = "no-store"
    except Exception:
        pass
    return resp
