#!/usr/bin/env python3
"""
Proactive guard: TTS first-call check (CSRF-aware) without test_client context manager
to avoid Flask request context teardown issues.

Expectations:
  - Prints "PASS: tts first call 200" if POST /api/v1/voice/tts-with-visemes returns 200 with CSRF.
  - Prints "FAIL: tts first call not 200" otherwise and exits 1.
"""
import sys
from importlib import import_module

def main():
    try:
        pkg = import_module("app")
    except Exception as e:
        print(f"FAIL: could not import app: {e}")
        return 1

    # Flask app must be accessible as pkg.app
    try:
        flask_app = getattr(pkg, "app")
    except Exception as e:
        print(f"FAIL: app not found on app package: {e}")
        return 1

    client = flask_app.test_client()

    # Fetch CSRF token
    csrf = None
    try:
        r = client.get("/api/v1/auth/csrf")
        data = None
        try:
            data = r.get_json(silent=True)
        except Exception:
            data = None
        if isinstance(data, dict):
            csrf = data.get("csrf") or data.get("token")
        # Fallback: some implementations return token in header
        if not csrf:
            csrf = r.headers.get("X-CSRF-Token")
    except Exception as e:
        print(f"FAIL: could not fetch CSRF: {e}")
        return 1

    headers = {}
    if csrf:
        headers["X-CSRF-Token"] = csrf

    # First TTS call
    try:
        resp = client.post(
            "/api/v1/voice/tts-with-visemes",
            json={"text": "proactive-check-hello", "session_id": "proactive-guard"},
            headers=headers
        )
    except Exception as e:
        print(f"FAIL: tts request errored: {e}")
        return 1

    if getattr(resp, "status_code", None) != 200:
        body_preview = None
        try:
            body_preview = (resp.data or b"")[:200]
        except Exception:
            body_preview = b""
        print("FAIL: tts first call not 200")
        try:
            print(f"DEBUG: status={resp.status_code} body={body_preview!r}")
        except Exception:
            pass
        return 1

    print("PASS: tts first call 200")
    return 0

if __name__ == "__main__":
    sys.exit(main())
