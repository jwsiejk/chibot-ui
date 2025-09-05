#!/usr/bin/env python3
import os, sys, json, base64
sys.path.insert(0, ".")
os.environ.setdefault("RATE_LIMIT_WINDOW_S","0.05")
os.environ.setdefault("RATE_LIMIT_MAX","100")
import app

# Provide vendor envs for provider init
os.environ.setdefault("ELEVENLABS_API_KEY","TEST")
os.environ.setdefault("ELEVENLABS_VOICE_ID","TESTVOICE")
os.environ.setdefault("OPENAI_API_KEY","TEST")

# Monkeypatch urllib for TTS and alignment, crafting duration to match alignment end
import urllib.request

ALIGN_END_MS = 1200  # 1.2s alignment

def mp3_len_for_ms(ms: int, kbps: int = 128) -> int:
    # bytes = seconds * (kbps*1000)/8
    return int((ms/1000.0) * (kbps*1000)/8)

AUDIO_BYTES = b"0" * mp3_len_for_ms(ALIGN_END_MS, 128)

class _FakeResp:
    def __init__(self, data: bytes): self._d = data
    def read(self): return self._d
    def __enter__(self): return self
    def __exit__(self, *a): return False

def _fake_urlopen(req, timeout=30):
    url = req.full_url if hasattr(req, "full_url") else str(req)
    if "/text-to-speech/" in url and "/alignment" not in url:
        # TTS audio
        return _FakeResp(AUDIO_BYTES)
    if url.endswith("/alignment"):
        payload = {
            "phonemes": [
                {"start_ms": 0, "phoneme": "HH"},
                {"start_ms": 200, "phoneme": "EH"},
                {"start_ms": 400, "phoneme": "L"},
                {"start_ms": 600, "phoneme": "OW"},
                {"start_ms": 900, "phoneme": "W"},
                {"start_ms": 1100, "phoneme": "ER"},
            ]
        }
        return _FakeResp(json.dumps(payload).encode("utf-8"))
    # Unknown -> fail test
    return _FakeResp(b"{}")

urllib.request.urlopen = _fake_urlopen

def A(c,m):
    print(("PASS" if c else "FAIL")+": "+m)
    return c

ok = True
client = app.create_app().test_client()
r = client.post("/api/v1/chat/tts-with-visemes", json={"text":"hello world"})
ok &= A(r.status_code == 200, "tts-with-visemes returns 200")
j = r.get_json()
ok &= A(j.get("ok") is True, "json ok")
vis = j.get("visemes") or []
ok &= A(len(vis) >= 3, "visemes present")
# monotonic times
times = [int(v.get("t_ms",0)) for v in vis]
ok &= A(all(x<y for x,y in zip(times, times[1:])), "viseme times strictly increasing")
# duration sanity within ±2% based on audio bytes length
audio = base64.b64decode(j.get("audio_b64",""))
est_ms = (len(audio) * 8 / 128000.0) * 1000.0
end_ms = times[-1]
pct = abs(end_ms - est_ms) / est_ms if est_ms else 0.0
ok &= A(pct <= 0.02, "end time within ±2% of estimated audio duration")

print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
