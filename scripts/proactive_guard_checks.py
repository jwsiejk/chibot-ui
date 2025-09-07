#!/usr/bin/env python3
"""
Proactive guard: TTS first-call check (production layout aware)
- Prefers ASGI app from app.asgi_gateway (TestClient)
- Else uses a Flask *instance* from the app package (test_client), avoiding class binding errors
- Adds CSRF handshake (GET /api/v1/auth/csrf -> X-CSRF-Token)
- Avoids Flask test_client context-manager teardown issue
Emits exactly the PASS/FAIL lines expected by CI.
"""
import sys
from importlib import import_module
from pathlib import Path
import inspect

# Ensure project root (parent of /scripts) is on sys.path
THIS_DIR = Path(__file__).resolve().parent
ROOT_DIR = THIS_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

def _get_asgi_client():
    # Try to load ASGI app from app.asgi_gateway
    try:
        gw = import_module("app.asgi_gateway")
    except Exception:
        return None
    for name in ("asgi", "app", "application"):
        asgi_app = getattr(gw, name, None)
        if asgi_app is not None:
            try:
                from starlette.testclient import TestClient
                return TestClient(asgi_app, base_url='https://testserver')
            except Exception:
                return None
    return None

def _get_flask_client(pkg):
    # Try to find a Flask *instance* on the app package
    try:
        from flask import Flask
    except Exception:
        Flask = None
    candidates = []
    for name in ("app", "flask_app", "application"):
        if hasattr(pkg, name):
            candidates.append(getattr(pkg, name))

    # Also scan all attrs for something that looks like a Flask instance
    for obj in pkg.__dict__.values():
        if obj not in candidates:
            candidates.append(obj)

    for obj in candidates:
        # Skip modules and classes
        if inspect.ismodule(obj) or inspect.isclass(obj):
            continue
        # If Flask available, require an instance
        if Flask is not None and isinstance(obj, Flask):
            try:
                return obj.test_client()
            except Exception:
                continue
        # Fallback duck-typing: bound method test_client and wsgi_app present
        if hasattr(obj, "test_client") and hasattr(obj, "wsgi_app") and not inspect.isclass(obj):
            try:
                # Ensure this is a *bound* method (has __self__)
                tc = getattr(obj, "test_client", None)
                if callable(tc):
                    # Bound method should have __self__ == obj in most cases
                    if getattr(tc, "__self__", None) is obj or Flask is None:
                        return tc()
            except Exception:
                continue
    return None

def _fetch_csrf(client):
    try:
        r = client.get("/api/v1/auth/csrf")
    except Exception as e:
        print(f"FAIL: could not fetch CSRF: {e}")
        return None
    csrf = None
    data = None
    # Flask response
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
    if isinstance(data, dict):
        csrf = data.get("csrf") or data.get("token")
    if not csrf and hasattr(r, "headers"):
        csrf = r.headers.get("X-CSRF-Token")
    if not csrf:
        print("FAIL: could not fetch CSRF: empty token")
        return None
    return csrf

def main():
    # Prefer ASGI
    client = _get_asgi_client()
    mode = "asgi" if client is not None else None

    if client is None:
        # Fallback to Flask instance on app package
        try:
            pkg = import_module("app")
        except Exception as e:
            print(f"FAIL: could not import app: {e}")
            return 1
        client = _get_flask_client(pkg)
        if client is None:
            print("FAIL: app not found on app package: no Flask or ASGI app exposed")
            return 1
        mode = "flask"

    # CSRF handshake
    csrf = _fetch_csrf(client)
    if not csrf:
        return 1
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
        # Best-effort short body preview
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
