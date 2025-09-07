#!/usr/bin/env python3
import os, sys, json
from pathlib import Path
import pathlib
ROOT=pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("RATE_LIMIT_WINDOW_S","0.05")
os.environ.setdefault("RATE_LIMIT_MAX","100")
os.environ["CSRF_ENFORCED"]=""

from importlib import import_module
app = import_module('app')


# Test env for vendors
os.environ.setdefault("OPENAI_API_KEY","TEST")
os.environ.setdefault("OPENAI_MODEL","gpt-4o-mini")
os.environ.setdefault("ELEVENLABS_API_KEY","TEST")
os.environ.setdefault("ELEVENLABS_VOICE_ID","TESTVOICE")
os.environ.setdefault("EMAIL_HOST","smtp.test")
os.environ.setdefault("EMAIL_PORT","587")
os.environ.setdefault("EMAIL_HOST_USER","user")
os.environ.setdefault("EMAIL_HOST_PASSWORD","pass")
os.environ.setdefault("FROM_EMAIL","chip@example.com")
os.environ.setdefault("EMAIL_USE_TLS","true")

# Monkeypatch urllib + smtplib to simulate failures/success
import urllib.request, smtplib
_call_counts = {"tts":0, "openai":0}

_real_urlopen = urllib.request.urlopen
class _FakeResp:
    def __init__(self, data: bytes): self._d = data
    def read(self): return self._d
    def __enter__(self): return self
    def __exit__(self, *a): return False

def _fake_urlopen(req, timeout=30):
    url = req.full_url if hasattr(req, "full_url") else str(req)
    if "chat/completions" in url:
        _call_counts["openai"] += 1
        return _FakeResp(json.dumps({"choices":[{"message":{"content":"ok"}}]}).encode("utf-8"))
    if "elevenlabs" in url or "api.elevenlabs" in url:
        _call_counts["tts"] += 1
        # First attempt fails to trigger retry, then succeed
        if _call_counts["tts"] == 1:
            raise OSError("simulated timeout")
        return _FakeResp(b"FAKE_MP3_DATA")
    if "audio/transcriptions" in url:
        return _FakeResp(json.dumps({"text":"hello"}).encode("utf-8"))
    raise OSError('simulated network fail')

urllib.request.urlopen = _fake_urlopen

class _FakeSMTP:
    send_count = 0
    def __init__(self, host, port, timeout=30): pass
    def starttls(self): pass
    def login(self, u, p): pass
    def sendmail(self, frm, to, msg): _FakeSMTP.send_count += 1
    def quit(self): pass

smtplib.SMTP = _FakeSMTP

def A(c,m):
    print(("PASS" if c else "FAIL")+": "+m)
    return c

ok = True

flask_app = app.create_app()
client = flask_app.test_client()

# 1) TTS retry then success, and idempotency cache
r1 = client.post("/api/v1/chat/tts-with-visemes", json={"text":"hello world"})
ok &= A(r1.status_code == 200, "TTS first call returns 200 after retry")
first_calls = _call_counts["tts"]
r2 = client.post("/api/v1/chat/tts-with-visemes", json={"text":"hello world"})
ok &= A(r2.status_code == 200, "TTS second call returns 200")
ok &= A(_call_counts["tts"] == first_calls, "TTS idempotency prevented second vendor call")

# 2) Mailer idempotency
from app.services.mailer import send_transcript
send_transcript("u@example.com","S","B")
send_transcript("u@example.com","S","B")
ok &= A(_FakeSMTP.send_count == 1, "Mailer idempotency prevented duplicate send")

# 3) Circuit breaker opens after successive failures (via httputil)
from app.services.httputil import breaker_is_open, http_json
tripped = False
for i in range(4):
    try:
        http_json("https://api.unknown.example/fail", payload={}, headers={}, breaker_key="test.cb", breaker_threshold=2, breaker_cooldown=5, timeout=0.1, retries=0)
    except Exception:
        pass
    if breaker_is_open("test.cb", recovery_timeout=5):
        tripped = True
        break
ok &= A(tripped, "Circuit breaker opened after failures")




# === WS handshake & ping/pong (protocol-aware) ===
try:
    from importlib import import_module
    gw = import_module("app.asgi_gateway")
    star_asgi = getattr(gw, "asgi", None)
    if star_asgi is None:
        raise AssertionError("ASGI gateway missing 'asgi' app")
    from starlette.testclient import TestClient
    with TestClient(star_asgi) as tc:
        with tc.websocket_connect("/ws/v1/chat?session_id=phase10") as ws:
            first = ws.receive_text()
            import json as _json
            msg = _json.loads(first)
            ok &= A(msg.get("type") == "ready", "WS handshake sends ready")
            ok &= A("proto" in msg and isinstance(msg.get("heartbeat_ms"), int), "WS ready includes proto/heartbeat_ms")
            ws.send_text("ping")
            pong = ws.receive_text()
            ok &= A(pong == "pong", "WS ping -> pong")
except Exception as e:
    print("WS CHECK ERROR:", repr(e))
    ok &= A(False, "WS handshake & ping")
print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)


# === WS handshake & ping/pong (protocol-aware) ===
try:
    from importlib import import_module
    gw = import_module("app.asgi_gateway")
    star_asgi = getattr(gw, "asgi", None)
    if star_asgi is None:
        raise AssertionError("ASGI gateway missing 'asgi' app")
    from starlette.testclient import TestClient
    with TestClient(star_asgi) as tc:
        with tc.websocket_connect("/ws/v1/chat?session_id=phase10") as ws:
            first = ws.receive_text()
            try:
                import json as _json
                msg = _json.loads(first)
            except Exception as _e:
                raise AssertionError(f"WS first frame not JSON: {first!r}")
            ok &= A(msg.get("type") == "ready", "WS handshake sends ready")
            ok &= A("proto" in msg and isinstance(msg["heartbeat_ms"], int), "WS ready includes proto/heartbeat_ms")
            # ping/pong
            ws.send_text("ping")
            pong = ws.receive_text()
            ok &= A(pong == "pong", "WS ping -> pong")
except Exception as e:
    print("WS CHECK ERROR:", repr(e))
    ok &= A(False, "WS handshake & ping")
