
#!/usr/bin/env python3
import os, re, sys
ROOT = os.path.dirname(os.path.dirname(__file__))
def read(p): return open(p,"r",encoding="utf-8",errors="ignore").read()

ok=True
def A(c,m):
  global ok
  print(("PASS" if c else "FAIL")+": "+m); ok = ok and c

aud = read(os.path.join(ROOT,"static/js/audio.js"))
A("new Audio()" in aud and "Blob(" in aud, "audio.js uses real HTMLAudioElement + Blob for MP3 playback")

adm = read(os.path.join(ROOT,"app/api_v1/admin.py"))
A("/kb/docs" in adm and "kb_seed" in adm, "admin knowledge endpoints present")

ui = read(os.path.join(ROOT,"templates/admin.html"))
A("tab-knowledge" in ui, "Admin Knowledge tab present")

# linter + earlier checks
import subprocess
p6=subprocess.run([sys.executable, os.path.join(ROOT,"scripts","phase6_checks.py")], capture_output=True, text=True)
p7=subprocess.run([sys.executable, os.path.join(ROOT,"scripts","phase7_checks.py")], capture_output=True, text=True)
p71=subprocess.run([sys.executable, os.path.join(ROOT,"scripts","phase7_1_checks.py")], capture_output=True, text=True)
p8=subprocess.run([sys.executable, os.path.join(ROOT,"scripts","phase8_checks.py")], capture_output=True, text=True)
lint=subprocess.run([sys.executable, os.path.join(ROOT,"scripts","route_linter.py")], capture_output=True, text=True)
print(p6.stdout); print(p7.stdout); print(p71.stdout); print(p8.stdout); print(lint.stdout)
A(all(x.returncode==0 for x in [p6,p7,p71,p8,lint]), "all prior phase checks pass + route linter")

print("\\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
