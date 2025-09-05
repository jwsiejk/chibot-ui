
#!/usr/bin/env python3
import os, re, sys
ROOT = os.path.dirname(os.path.dirname(__file__))
def read(p): return open(p,"r",encoding="utf-8",errors="ignore").read()

ok=True
def A(c,m):
  global ok
  print(("PASS" if c else "FAIL")+": "+m)
  ok = ok and c

# Provider modules exist
A(os.path.exists(os.path.join(ROOT,"app/services/tts_provider.py")), "tts_provider exists")
A(os.path.exists(os.path.join(ROOT,"app/services/stt_provider.py")), "stt_provider exists")
A(os.path.exists(os.path.join(ROOT,"app/services/providers/elevenlabs_tts.py")), "elevenlabs tts provider exists")
A(os.path.exists(os.path.join(ROOT,"app/services/providers/whisper_stt.py")), "whisper stt provider exists")

# voice endpoints use providers
voice = read(os.path.join(ROOT,"app/api_v1/voice.py"))
A("get_tts_provider(" in voice, "voice tts endpoint uses tts_provider")
A(("get_stt_provider(" in voice) or ("from ..services.stt_provider import get_stt_provider" in voice), "voice stt endpoint uses stt_provider")

# chat tts endpoint uses provider
chat = read(os.path.join(ROOT,"app/api_v1/chat.py"))
A(("get_tts_provider(" in chat) or ("from ..services.tts_provider import get_tts_provider" in chat), "chat tts endpoint uses tts_provider")

# default config keys present
db = read(os.path.join(ROOT,"app/db.py"))
A("'stt_provider'" in db and "'tts_provider'" in db, "config contains stt_provider/tts_provider")

# linter still passes
import subprocess
proc = subprocess.run([sys.executable, os.path.join(ROOT,"scripts","route_linter.py")], capture_output=True, text=True)
print(proc.stdout)
A(proc.returncode == 0, "route linter passes")

print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
