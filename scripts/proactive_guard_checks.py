#!/usr/bin/env python3
"""
Proactive guard: TTS first-call check
- Robust import (adds project root to sys.path)
- Works with either a Flask app (has .test_client) or an ASGI app (app.asgi_gateway:asgi)
- CSRF handshake included
- Avoids Flask test_client context-manager teardown issues
Emits the exact PASS/FAIL lines expected by the harness.
"""
import sys
from importlib import import_module
from pathlib import Path

# Ensure project root (parent of /scripts) is on sys.path
THIS_DIR = Path(__file__).resolve().parent
ROOT_DIR = THIS_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

def _find_flask_app(pkg):
    # Common attribute names
    for name in ("app", "flask_app", "application"):
        obj = getattr(pkg, name, None)
        if obj is not None and hasattr(obj, "test_client"):
            return obj
    # Duck-type search over module dict
    for obj in pkg.__dict__.values():
        if hasattr(obj, "test_client"):
            return obj
    return None

def _find_asgi_app():
    try:
        gw = import_module("app.asgi_gateway")
    except Exception:
        return None
    for name in ("asgi", "app", "application"):
        obj = getattr(gw, name, None)
        if obj is not None:
            return obj
    return None

def main():
    # Import package
    try:
        pkg = import_module("app")
    except Exception as e:
        print(f"FAIL: could not import app: {e}")
        return 1

    # Prefer Flask app if exposed
    flask_app = _find_flask_app(pkg)
    client = None
    mode = None

    if flask_app is not None:
        try:
            client = flask_app.test_client()
            mode = "flask"
        except Exception as e:
            print(f"FAIL: could not create flask test_client: {e}")
            return 1
    else:
        # Try ASGI app (Starlette TestClient)
        asgi_app = _find_asgi_app()
        if asgi_app is None:
            # Nothing usable found
            print("FAIL: app not found on app package: no Flask or ASGI app exposed")
            return 1
        try:
            from starlette.testclient import TestClient  # provided by starlette/httpx
            client = TestClient(asgi_app)
            mode = "asgi"
        except Exception as e:
            print(f"FAIL: could not create ASGI TestClient: {e}")
            return 1

    # CSRF handshake
    try:
        r = client.get("/api/v1/auth/csrf")
        csrf = None
        data = None
        # Flask response
        if hasattr(r, "get_json"):
            data = r.get_json(silent=True)
        # Starlette response
        elif hasattr(r, "json"):
            try:
                data = r.json()
            except Exception:
                data = None
        if isinstance(data, dict):
            csrf = data.get("csrf") or data.get("token")
        if not csrf:
            # Try header in both clients
            hdrs = getattr(r, "headers", {})
            csrf = hdrs.get("X-CSRF-Token") if hdrs else None
        if not csrf:
            print("FAIL: could not fetch CSRF: empty token")
            return 1
    except Exception as e:
        print(f"FAIL: could not fetch CSRF: {e}")
        return 1

    headers = {"X-CSRF-Token": csrf}

    # First TTS call
    try:
        if mode == "flask":
            resp = client.post(
                "/api/v1/voice/tts-with-visemes",
                json={"text": "proactive-check-hello", "session_id": "proactive-guard"},
                headers=headers,
            )
            status = getattr(resp, "status_code", None)
            body_preview = (resp.data or b"")[:200] if hasattr(resp, "data") else b""
        else:
            # ASGI
            resp = client.post(
                "/api/v1/voice/tts-with-visemes",
                json={"text": "proactive-check-hello", "session_id": "proactive-guard"},
                headers=headers,
            )
            status = getattr(resp, "status_code", None)
            # Starlette TestClient returns str/bytes in content
            body_preview = getattr(resp, "content", b"")[:200]
    except Exception as e:
        print(f"FAIL: tts request errored: {e}")
        return 1

    if status != 200:
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
