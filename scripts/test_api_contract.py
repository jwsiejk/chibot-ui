#!/usr/bin/env python3
import json, sys, os, types, io
from typing import Any, Dict
sys.path.insert(0, ".")

# --- Test env: set fake keys so real providers initialize ---
os.environ.setdefault("OPENAI_API_KEY","TEST")
os.environ.setdefault("OPENAI_MODEL","gpt-4o-mini")
os.environ.setdefault("ELEVENLABS_API_KEY","TEST")
os.environ.setdefault("ELEVENLABS_VOICE_ID","TESTVOICE")
os.environ.setdefault("EMAIL_HOST","smtp.test")
os.environ.setdefault("EMAIL_PORT","587")
os.environ.setdefault("EMAIL_HOST_USER","user")
os.environ.setdefault("EMAIL_HOST_PASSWORD","pass")
os.environ.setdefault("FROM_EMAIL","chip@example.com")

# # Monkeypatch urllib.request.urlopen to avoid real network, but keep real provider logic
import urllib.request, json as _json, base64 as _b64

class _FakeResp:
    def __init__(self, data: bytes): self._d = data
    def read(self): return self._d
    def __enter__(self): return self
    def __exit__(self, *a): return False

_real_urlopen = urllib.request.urlopen

def _fake_urlopen(req, timeout=30):
    url = req.full_url if hasattr(req, "full_url") else str(req)
    # OpenAI chat completions
    if "/v1/chat/completions" in url or "chat/completions" in url:
        payload = {"choices":[{"message":{"content":"Test OK"}}]}
        return _FakeResp(_json.dumps(payload).encode("utf-8"))
    # ElevenLabs alignment
    if url.endswith("/alignment"):
        payload = {"phonemes":[
            {"start_ms":0,"phoneme":"HH"},
            {"start_ms":120,"phoneme":"EH"},
            {"start_ms":260,"phoneme":"L"}
        ]}
        return _FakeResp(_json.dumps(payload).encode("utf-8"))
    # ElevenLabs TTS audio endpoint → return small MP3-ish bytes
    if "/text-to-speech/" in url and "/alignment" not in url:
        return _FakeResp(b"FAKE_MP3_DATA_BYTES")
    # Whisper STT
    if "/v1/audio/transcriptions" in url:
        payload = {"text":"hello world"}
        return _FakeResp(_json.dumps(payload).encode("utf-8"))
    return _FakeResp(b"{}")

urllib.request.urlopen = _fake_urlopen
import smtplib
class _FakeSMTP:
    def __init__(self, host, port): self.host, self.port = host, port
    def starttls(self): pass
    def login(self, u, p): pass
    def sendmail(self, from_addr, to_addrs, msg): pass
    def quit(self): pass
smtplib.SMTP = _FakeSMTP

os.environ.setdefault("RATE_LIMIT_WINDOW_S","0.05")
os.environ.setdefault("RATE_LIMIT_MAX","100")
os.environ.setdefault("RATE_LIMIT_WINDOW_S","0.05")
os.environ.setdefault("RATE_LIMIT_MAX","100")
import app
app_ = app.create_app()
client = app_.test_client()

def assert_true(cond, msg):
    if not cond:
        print("FAIL:", msg); return False
    print("PASS:", msg); return True

ok = True

# Greet
r = client.get("/api/v1/greet?session_id=testsid")
ok &= assert_true(r.status_code == 200, "GET /api/v1/greet returns 200")
ok &= assert_true(r.is_json, "greet returns JSON")
j = r.get_json()
ok &= assert_true(j.get("ok") is True and "turn_id" in j, "greet JSON ok + turn_id")

# Chat
r = client.post("/api/v1/chat", json={"text":"Test message"})
ok &= assert_true(r.status_code in (200,202), "POST /api/v1/chat returns 200/202")
ok &= assert_true(r.is_json, "chat returns JSON")

# TTS
r = client.post("/api/v1/chat/tts-with-visemes", json={"text":"Hello world"})
ok &= assert_true(r.status_code == 200, "POST /api/v1/chat/tts-with-visemes returns 200")
j = r.get_json()
ok &= assert_true(j.get("ok") is True and isinstance(j.get("audio_b64"), str) and isinstance(j.get("visemes"), list), "tts JSON ok + audio_b64 + visemes present")

# STT
r = client.post("/api/v1/voice/stt", data=b"FAKE", content_type="audio/webm")
ok &= assert_true(r.status_code in (200,400), "POST /api/v1/voice/stt returns 200/400")
ok &= assert_true(r.is_json, "stt returns JSON")

# Admin logs SSE
r = client.get("/api/v1/admin/logs")
ok &= assert_true(r.status_code in (200, 206), "GET /api/v1/admin/logs returns OK-ish")

print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
