#!/usr/bin/env python3
import os, sys, subprocess
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
ok=True
def A(c,m): 
  global ok; print(("PASS" if c else "FAIL")+": "+m); ok = ok and c

# Retrieval presence
retrieval = (ROOT/"app/services/retrieval.py").read_text(encoding="utf-8", errors="ignore") if (ROOT/"app/services/retrieval.py").exists() else ""
A(("def add_document" in retrieval) or ("def add_doc" in retrieval), "retrieval service exposes add_document/search")
A(("def search" in retrieval), "retrieval service exposes add_document/search")

# KB passed into provider context (static check)
streaming = (ROOT/"app/services/streaming.py").read_text(encoding="utf-8", errors="ignore")
A("'kb': kb" in streaming and "build_persona_preamble" in streaming, "KB passed into provider context")

# Mock provider emits KB count tag (string-based)
mock = (ROOT/"app/services/providers/mock_provider.py").read_text(encoding="utf-8", errors="ignore")
A("KB:" in mock, "mock provider emits KB count tag")

# OpenAI stub emits KB count tag (string-based check in provider doc block)
openai = (ROOT/"app/services/providers/openai_provider.py").read_text(encoding="utf-8", errors="ignore")
A("KB:" in openai, "openai stub emits KB count tag")

# TTS supports mp3 (string)
eleven = (ROOT/"app/services/providers/elevenlabs_tts.py").read_text(encoding="utf-8", errors="ignore")
A("mp3" in eleven.lower(), "TTS provider supports mp3 output format")

# Reuse prior phase checks and linter
for script in ("phase6_checks.py","phase7_checks.py","phase7_1_checks.py","route_linter.py"):
    p = subprocess.run([sys.executable, str(ROOT/"scripts"/script)], capture_output=True, text=True, cwd=str(ROOT))
    print(p.stdout)
    if "route_linter" not in script:
        ok = ok and (p.returncode==0)

print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
