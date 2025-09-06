#!/usr/bin/env python3
import os, sys, re, subprocess
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
ok=True
def A(c,m):
  global ok; print(("PASS" if c else "FAIL")+": "+m); ok = ok and c

sys.path.insert(0, str(ROOT))
# Abstraction present
A((ROOT/"app/services/llm_provider.py").exists(), "llm_provider abstraction present")
# Streaming loads provider
streaming = (ROOT/"app/services/streaming.py").read_text(encoding="utf-8", errors="ignore")
A("from .llm_provider import get_provider" in streaming, "streaming loads provider")

# openai provider string-based checks
openai = (ROOT/"app/services/providers/openai_provider.py").read_text(encoding="utf-8", errors="ignore")
A("urllib.request" in openai and "OPENAI_API_KEY" in openai, "openai provider: network path present")
A(("fallback" in openai) or ("openai-stub" in openai), "openai provider: fallback path present when no key")
A('os.environ.get("OPENAI_MODEL"' in openai or 'os.getenv("OPENAI_MODEL")' in openai, "openai model env read")

# linter
p = subprocess.run([sys.executable, str(ROOT/"scripts/route_linter.py")], capture_output=True, text=True, cwd=str(ROOT))
print(p.stdout); A(p.returncode==0, "route linter passes")

print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
