#!/usr/bin/env python3
"""
Proactive guard: TTS first-call check (browser-faithful, production-correct)
- Prefer ASGI app from app.asgi_gateway (Starlette TestClient with HTTPS base_url to honor Secure cookies)
- Fallback to Flask app instance if needed
- CSRF handshake (GET /api/v1/auth/csrf), then ensure cookie is in the client jar
- POST /api/v1/voice/tts-with-visemes with X-CSRF-Token header; expect 200
Emits exactly:
  PASS: tts first call 200
  or
  FAIL: tts first call not 200
"""
import sys
from importlib import import_module
from pathlib import Path
import inspect

# Ensure project root (parent of /scripts) on sys.path
THIS_DIR = Path(__file__).resolve().parent
ROOT_DIR = THIS_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

def _get_asgi_client():
    try:
        gw = import_module("app.asgi_gateway")
    except Exception:
        return None
    for name in ("asgi", "app", "application"):
        asgi_app = getattr(gw, name, None)
        if asgi_app is not None:
            try:
                from starlette.testclient import TestClient
                # HTTPS base_url so Secure cookies are honored
                return TestClient(asgi_app, base_url="https://testserver")
            except Exception:
                return None
    return None

def _get_flask_client(pkg):
    try:
        from flask import Flask
    except Exception:
        Flask = None
    # scan attrs for a Flask *instance*
    for obj in pkg.__dict__.values():
        if Flask is not None and isinstance(obj, Flask):
            try:
                return obj.test_client()
            except Exception:
                continue
        if hasattr(obj, "test_client") and hasattr(obj, "wsgi_app") and not inspect.isclass(obj):
            try:
                return obj.test_client()
            except Exception:
                continue
    return None

def _fetch_csrf(client):
    try:
        r = client.get("/api/v1/auth/csrf")
    except Exception as e:
        print(f"FAIL: could not fetch CSRF: {e}")
        return None
    # Flask response
    data = None
    if hasattr(r, "get_json"):
        try:
            data = r.get_json(silent=True)
        except Exception:
            data = None
    # Starlette response
    elif hasattr(r, "json"):
        try:
            data = r.json()
        except Exception:
            data = None
    csrf = None
    if isinstance(data, dict):
        csrf = data.get("csrf") or data.get("token")
    if not csrf and hasattr(r, "headers"):
        csrf = r.headers.get("X-CSRF-Token")
    return csrf

def _ensure_cookie(client, csrf):
    # Ensure the CSRF cookie is present for the POST
    try:
        # Starlette/requests client
        jar = getattr(client, "cookies", None)
        if jar is not None:
            try:
                jar.set("csrf", csrf, domain="testserver", path="/")
            except Exception:
                # Some jars don't expose set(); ignore
                pass
        # Flask client fallback
        if hasattr(client, "set_cookie"):
            try:
                client.set_cookie("testserver", "csrf", csrf, path="/")
            except Exception:
                pass
    except Exception:
        pass

def main():
    # Prefer ASGI
    client = _get_asgi_client()
    if client is None:
        # Fallback to Flask app instance
        try:
            pkg = import_module("app")
        except Exception as e:
            print(f"FAIL: could not import app: {e}")
            return 1
        client = _get_flask_client(pkg)
        if client is None:
            print("FAIL: app not found on app package: no Flask or ASGI app exposed")
            return 1

    # CSRF handshake
    csrf = _fetch_csrf(client)
    if not csrf:
        print("FAIL: could not fetch CSRF: empty token")
        return 1

    # Make sure the cookie is present
    _ensure_cookie(client, csrf)

    headers = {"X-CSRF-Token": csrf}

    # First TTS call
    try:
        resp = client.post(
            "/api/v1/voice/tts-with-visemes",
            json={"text": "proactive-check-hello", "session_id": "proactive-guard"},
            headers=headers,
        )
    except Exception as e:
        print(f"FAIL: tts request errored: {e}")
        return 1

    status = getattr(resp, "status_code", None)
    if status != 200:
        # Body preview
        body_preview = b""
        if hasattr(resp, "data"):
            try:
                body_preview = (resp.data or b"")[:200]
            except Exception:
                body_preview = b""
        elif hasattr(resp, "content"):
            try:
                body_preview = (resp.content or b"")[:200]
            except Exception:
                body_preview = b""
        print("FAIL: tts first call not 200")
        try:
            print(f"DEBUG: status={status} body={body_preview!r}")
        except Exception:
            pass
        return 1

    print("PASS: tts first call 200")
    return 0

if __name__ == "__main__":
    sys.exit(main())
