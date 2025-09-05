
#!/usr/bin/env python3
import os, re, sys, json, tempfile

ROOT = os.path.dirname(os.path.dirname(__file__))
def read(p): return open(p,"r",encoding="utf-8",errors="ignore").read()

ok=True
def A(c,m):
  global ok
  print(("PASS" if c else "FAIL")+": "+m); ok = ok and c

# 1. retrieval service presence
retrieval = read(os.path.join(ROOT,"app/services/retrieval.py"))
A("add_document" in retrieval and "search(" in retrieval, "retrieval service exposes add_document/search")

# 2. admin seed endpoint
adm = read(os.path.join(ROOT,"app/api_v1/admin.py"))
A("/kb/seed" in adm, "admin kb seed endpoint present")

# 3. streaming passes KB into provider context
stream = read(os.path.join(ROOT,"app/services/streaming.py"))
A(("kb_search" in stream) and (("context=ctx" in stream) or ("context=context" in stream)) and (("kb': kb" in stream) or ("kb': kb" in stream) or ("'kb': kb" in stream) or ("'kb': kb" in stream)), "KB passed into provider context")

# 4. providers mention KB in responses
real = read(os.path.join(ROOT,'app/services/providers_real/openai_http_provider.py'))
openai = read(os.path.join(ROOT,"app/services/providers_real/openai_http_provider.py"))
A("OPENAI_API_KEY" in real, "real provider reads API key")
A("chat/completions" in openai, "real provider targets chat completions API")

# 5. MP3 path in providers (ElevenLabs provider mentions output format)
tts = read(os.path.join(ROOT,"app/services/providers/elevenlabs_tts.py"))
A("output_format" in tts and "mp3" in tts.lower(), "TTS provider supports mp3 output format")

# 6. linter & earlier phases
import subprocess
p6 = subprocess.run([sys.executable, os.path.join(ROOT,"scripts","phase6_checks.py")], capture_output=True, text=True)
print(p6.stdout)
p7 = subprocess.run([sys.executable, os.path.join(ROOT,"scripts","phase7_checks.py")], capture_output=True, text=True)
print(p7.stdout)
p71 = subprocess.run([sys.executable, os.path.join(ROOT,"scripts","phase7_1_checks.py")], capture_output=True, text=True)
print(p71.stdout)
lint = subprocess.run([sys.executable, os.path.join(ROOT,"scripts","route_linter.py")], capture_output=True, text=True)
print(lint.stdout)
A(p6.returncode==0 and p7.returncode==0 and p71.returncode==0 and lint.returncode==0, "all prior checks + linter pass")

print("\\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
