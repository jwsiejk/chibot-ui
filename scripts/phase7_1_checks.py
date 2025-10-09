#!/usr/bin/env python3
import os, sys, subprocess
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
ok=True
def A(c,m):
  global ok; print(("PASS" if c else "FAIL")+": "+m); ok = ok and c

# Providers exist
A((ROOT/"app/services/tts_provider.py").exists(), "tts_provider exists")
A((ROOT/"app/services/stt_provider.py").exists(), "stt_provider exists")
A((ROOT/"app/services/providers/elevenlabs_tts.py").exists(), "elevenlabs tts provider exists")
A((ROOT/"app/services/providers/whisper_stt.py").exists(), "whisper stt provider exists")

# Endpoints wire providers (static check)
voice = (ROOT/"app/api_v1/voice.py").read_text(encoding="utf-8", errors="ignore")
A("get_stt_provider" in voice, "voice stt endpoint uses stt_provider")
chat = (ROOT/"app/api_v1/chat.py").read_text(encoding="utf-8", errors="ignore")
A("get_tts_provider" in chat, "chat tts endpoint uses tts_provider")

# Config keys present
db = (ROOT/"app/db.py").read_text(encoding="utf-8", errors="ignore")
A("'stt_provider'" in db and "'tts_provider'" in db, "config contains stt_provider/tts_provider")

# linter
p = subprocess.run([sys.executable, str(ROOT/"scripts/route_linter.py")], capture_output=True, text=True, cwd=str(ROOT))
print(p.stdout); A(p.returncode==0, "route linter passes")

print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
