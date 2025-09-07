#!/usr/bin/env python3
import sys
from importlib import import_module
from pathlib import Path
import inspect

THIS_DIR = Path(__file__).resolve().parent
ROOT_DIR = THIS_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

def _asgi_client():
    try:
        gw = import_module("app.asgi_gateway")
    except Exception:
        return None
    for name in ("asgi","app","application"):
        app = getattr(gw, name, None)
        if app is not None:
            try:
                from starlette.testclient import TestClient
                return TestClient(app, base_url="https://testserver")
            except Exception:
                return None
    return None

def _flask_client():
    try:
        pkg = import_module("app")
    except Exception:
        return None
    try:
        from flask import Flask
    except Exception:
        Flask = None
    for obj in pkg.__dict__.values():
        if Flask and isinstance(obj, Flask):
            try: return obj.test_client()
            except Exception: continue
        if hasattr(obj,"test_client") and hasattr(obj,"wsgi_app") and not inspect.isclass(obj):
            try: return obj.test_client()
            except Exception: continue
    return None

def _get_csrf(client):
    r = client.get("/api/v1/auth/csrf")
    data = None
    if hasattr(r,"get_json"):
        try: data = r.get_json(silent=True)
        except Exception: data = None
    elif hasattr(r,"json"):
        try: data = r.json()
        except Exception: data = None
    token = (data or {}).get("csrf") or (hasattr(r,"headers") and r.headers.get("X-CSRF-Token"))
    return token

def _ensure_cookie(client, csrf):
    try:
        jar = getattr(client,"cookies",None)
        if jar is not None:
            try: jar.set("csrf", csrf, domain="testserver", path="/")
            except Exception: pass
        if hasattr(client,"set_cookie"):
            try: client.set_cookie("testserver","csrf",csrf,path="/")
            except Exception: pass
    except Exception:
        pass

def main():
    client = _asgi_client() or _flask_client()
    if client is None:
        print("FAIL: app not found on app package: no Flask or ASGI app exposed")
        return 1

    csrf = _get_csrf(client)
    if not csrf:
        print("FAIL: could not fetch CSRF: empty token"); return 1

    _ensure_cookie(client, csrf)

    headers = {"X-CSRF-Token": csrf, "Cookie": f"csrf={csrf}"}

    try:
        resp = client.post(
            "/api/v1/voice/tts-with-visemes",
            json={"text":"proactive-check-hello","session_id":"proactive-guard"},
            headers=headers,
        )
    except Exception as e:
        print(f"FAIL: tts request errored: {e}")
        return 1

    status = getattr(resp,"status_code",None)
    if status != 200:
        # short body preview
        body = b""
        if hasattr(resp,"data"):
            try: body = (resp.data or b"")[:200]
            except Exception: body = b""
        elif hasattr(resp,"content"):
            try: body = (resp.content or b"")[:200]
            except Exception: body = b""
        print("FAIL: tts first call not 200")
        try: print(f"DEBUG: status={status} body={body!r}")
        except Exception: pass
        return 1

    print("PASS: tts first call 200")
    return 0

if __name__ == "__main__":
    sys.exit(main())
