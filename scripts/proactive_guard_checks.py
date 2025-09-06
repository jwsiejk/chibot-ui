#!/usr/bin/env python3
"""
Proactive guardrail checks for Ask Chip (runs in CI predeploy).
Covers: route lints, admin UI controls, config shape/defaults, SSE, TTS idempotency, runtime-fallback scan.
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

# 0) Prefer CI_DB_URL else local sqlite for safety
os.environ.setdefault("DATABASE_URL", os.environ.get("CI_DB_URL", "sqlite:///ci_acceptance.sqlite3"))
# Provide vendor envs so providers can init during tests
os.environ.setdefault("ELEVENLABS_API_KEY","TEST")
os.environ.setdefault("ELEVENLABS_VOICE_ID","TESTVOICE")
os.environ.setdefault("OPENAI_API_KEY","TEST")

# 1) Route linter (v1 only)
rc = os.system(f"{sys.executable} {ROOT/'scripts'/'route_linter.py'} > /dev/null")
if rc != 0:
    fail("route_linter.py failed")
ok("route-linter")

# 2) Admin UI controls present
admin_html = (ROOT/"templates"/"admin.html").read_text(encoding="utf-8", errors="ignore")
required_ids = ["cfg-audio_worklet_enabled","cfg-vad_attack_ms","cfg-vad_release_ms","cfg-vad_dbfs_threshold","cfgAudioSave","tab-config-audio"]
for rid in required_ids:
    if rid not in admin_html:
        fail(f"admin.html missing UI element: {rid}")
ok("admin UI controls")

# 3) Admin config endpoint returns expected shape and defaults
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

# 5) TTS idempotency at API layer (two same texts -> only one vendor network call)
_call_counts = {"tts":0}
_orig_urlopen = urllib.request.urlopen
class _FakeResp:
    def __init__(self, data: bytes): self._data = data
    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb): return False
    def read(self): return self._data

def _fake_urlopen(req, timeout=30):
    url = req.full_url if hasattr(req, "full_url") else str(req)
    if "elevenlabs" in url or "api.elevenlabs" in url:
        _call_counts["tts"] += 1
        if _call_counts["tts"] == 1:
            raise OSError("simulated timeout")
        return _FakeResp(b"FAKE_MP3_DATA")
    return _FakeResp(b"{}")

urllib.request.urlopen = _fake_urlopen
with app.test_client() as c:
    r1 = c.post("/api/v1/chat/tts-with-visemes", json={"text":"hello world"})
    if r1.status_code != 200: fail("tts first call not 200")
    first_calls = _call_counts["tts"]
    r2 = c.post("/api/v1/chat/tts-with-visemes", json={"text":"hello world"})
    if r2.status_code != 200: fail("tts second call not 200")
    if _call_counts["tts"] != first_calls:
        fail("API did not memoize TTS; vendor was called again")
urllib.request.urlopen = _orig_urlopen
ok("TTS idempotency (API memo)")

# 6) Runtime fallback scan (exclude tests/scripts)
bad = []
for p in ROOT.rglob("*.py"):
    sp = str(p)
    if "/tests/" in sp or "/scripts/" in sp:
        continue
    t = p.read_text(errors="ignore")
    if re.search(r"ALLOW_MOCK_PROVIDERS|MockTTS|mock_tts|mock_stt", t):
        bad.append(sp.replace(str(ROOT)+"/",""))
if bad:
    fail("runtime mock/fallback indicators found: " + ", ".join(bad))
ok("no runtime fallbacks")

print("\nPROACTIVE: PASS")