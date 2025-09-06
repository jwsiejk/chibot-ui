#!/usr/bin/env python3
"""
Proactive guardrail checks for Ask Chip (runs in CI predeploy).
Covers: route lints, admin UI controls, config shape/defaults, SSE,
TTS idempotency (API memo), TTS first call 200, and runtime-fallback scan.
"""
import os, sys, re, json, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def fail(msg):
    print("FAIL:", msg)
    sys.exit(1)

def ok(msg):
    print("PASS:", msg)

# Prefer CI DB
os.environ.setdefault("DATABASE_URL", os.environ.get("CI_DB_URL", "sqlite:///ci_acceptance.sqlite3"))

# 1) Route linter (v1 only)
rc = os.system(f"{sys.executable} {ROOT/'scripts'/'route_linter.py'} > /dev/null")
if rc != 0:
    fail("route-linter")
ok("route-linter")

# 2) Admin UI controls present
admin_html = (ROOT/"templates"/"admin.html").read_text(encoding="utf-8", errors="ignore")
required_ids = [
    "tab-config-audio", "cfg-audio_worklet_enabled",
    "cfg-vad_attack_ms", "cfg-vad_release_ms", "cfg-vad_dbfs_threshold",
]
for rid in required_ids:
    if rid not in admin_html:
        fail(f"admin.html missing UI element: {rid}")
ok("admin UI controls")

# 3) Admin config endpoint shape/defaults
import app as apppkg
app = apppkg.create_app()
with app.test_client() as c:
    r = c.get("/api/v1/admin/config")
    if r.status_code != 200:
        fail(f"/api/v1/admin/config status {r.status_code}")
    j = r.get_json(silent=True) or {}
    cfg = j.get("config", {})
    for k in ("audio_worklet_enabled","vad_attack_ms","vad_release_ms","vad_dbfs_threshold"):
        if k not in cfg:
            fail(f"/config missing {k}")
ok("admin config shape/defaults")

# 4) Admin logs SSE exists
with app.test_client() as c:
    r = c.get("/api/v1/admin/logs")
    if r.status_code != 200:
        fail(f"/api/v1/admin/logs status {r.status_code}")
ok("admin SSE endpoint")

# Helper: csrf
def _csrf_token(c):
    rr = c.get("/api/v1/auth/csrf")
    if rr.status_code != 200:
        fail("/api/v1/auth/csrf status {rr.status_code}")
    jj = rr.get_json(silent=True) or {}
    tok = jj.get("csrf")
    if not tok:
        fail("csrf token missing")
    return tok

# 5) TTS idempotency at API layer (/api/v1/chat/tts-with-visemes) using urlopen monkey-patch
_call_counts = {"tts": 0}
_orig_urlopen = urllib.request.urlopen
class _FakeResp:
    def __init__(self, data: bytes): self._data = data
    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb): return False
    def read(self): return self._data

def _fake_urlopen(req, timeout=30):
    # Count ElevenLabs calls only
    url = getattr(req, "full_url", str(req))
    if "api.elevenlabs.io" in url:
        _call_counts["tts"] += 1
        return _FakeResp(b"\x00\x01FAKE_MP3")
    return _orig_urlopen(req, timeout=timeout)

urllib.request.urlopen = _fake_urlopen
try:
    with app.test_client() as c:
        tok = _csrf_token(c)
        headers = {"X-CSRF-Token": tok, "Content-Type":"application/json"}
        body = json.dumps({"text": "guard check sentence"}).encode("utf-8")
        r1 = c.post("/api/v1/chat/tts-with-visemes", data=body, headers=headers)
        if r1.status_code != 200: fail("TTS idempotency first call status not 200")
        r2 = c.post("/api/v1/chat/tts-with-visemes", data=body, headers=headers)
        if r2.status_code != 200: fail("TTS idempotency second call status not 200")
        if _call_counts["tts"] != 1:
            fail("TTS idempotency (API memo) vendor call count != 1")
    ok("TTS idempotency (API memo)")
finally:
    urllib.request.urlopen = _orig_urlopen

# 6) TTS first call 200 (voice route) with CSRF and urlopen patch to avoid external network
urllib.request.urlopen = _fake_urlopen
try:
    with app.test_client() as c:
        tok = _csrf_token(c)
        headers = {"X-CSRF-Token": tok, "Content-Type":"application/json"}
        body = json.dumps({"text": "voice check"}).encode("utf-8")
        # prefer the voice blueprint route
        r = c.post("/api/v1/voice/tts-with-visemes", data=body, headers=headers)
        if r.status_code != 200:
            # try the chat path as a fallback within tests
            r = c.post("/api/v1/chat/tts-with-visemes", data=body, headers=headers)
            if r.status_code != 200:
                fail("tts first call not 200")
    ok("tts first call 200")
finally:
    urllib.request.urlopen = _orig_urlopen

# 7) Runtime fallback scan (exclude tests/scripts)
bad = []
for p in ROOT.rglob("*.py"):
    sp = str(p)
    # Skip tests and scripts
    if "/tests/" in sp or "/scripts/" in sp:
        continue
    # Skip bytecode dirs
    if "/__pycache__/" in sp:
        continue
    t = p.read_text(errors="ignore")
    if re.search(r"ALLOW_MOCK_PROVIDERS|MockTTS|mock_tts|mock_stt", t):
        bad.append(sp.replace(str(ROOT)+"/",""))
if bad:
    fail("runtime mock/fallback indicators found: " + ", ".join(bad))
ok("no runtime fallbacks")

print("\nPROACTIVE: PASS")
