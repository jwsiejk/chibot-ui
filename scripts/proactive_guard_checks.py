#!/usr/bin/env python3
"""
Proactive guard: TTS first-call check (CSRF-aware) with robust import path and
without Flask test_client context manager to avoid teardown errors.

Outputs exactly the lines the CI harness expects.
"""
import sys, os
from importlib import import_module
from pathlib import Path

# Ensure project root (parent of /scripts) is on sys.path
THIS_DIR = Path(__file__).resolve().parent
ROOT_DIR = THIS_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

def main():
    try:
        pkg = import_module("app")
    except Exception as e:
        print(f"FAIL: could not import app: {e}")
        return 1

    try:
        flask_app = getattr(pkg, "app")
    except Exception as e:
        print(f"FAIL: app not found on app package: {e}")
        return 1

    client = flask_app.test_client()

    # CSRF handshake
    try:
        r = client.get("/api/v1/auth/csrf")
        data = None
        try:
            data = r.get_json(silent=True)
        except Exception:
            data = None
        csrf = None
        if isinstance(data, dict):
            csrf = data.get("csrf") or data.get("token")
        if not csrf:
            csrf = r.headers.get("X-CSRF-Token")
        if not csrf:
            print("FAIL: could not fetch CSRF: empty token")
            return 1
    except Exception as e:
        print(f"FAIL: could not fetch CSRF: {e}")
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

    if getattr(resp, "status_code", None) != 200:
        body_preview = b""
        try:
            body_preview = (resp.data or b"")[:200]
        except Exception:
            pass
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
