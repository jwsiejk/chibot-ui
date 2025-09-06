#!/usr/bin/env python3
import os, sys, re
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
ok=True
def A(c,m):
  global ok
  print(("PASS" if c else "FAIL")+": "+m); ok = ok and c

# GET /api/v1/greet exists and returns 200
sys.path.insert(0, str(ROOT))
from app import create_app
from app.db import db as _memdb
_memdb.memory['configs']['llm_provider'] = 'mock'
app = create_app()
with app.test_client() as c:
    r = c.get("/api/v1/greet")
    A(r.status_code == 200, "GET /api/v1/greet -> 200")

# State machine & Start wiring (static checks)
state_js = (ROOT / "static/js/state.js").read_text(encoding="utf-8", errors="ignore")
A("STATES" in state_js and "READY" in state_js, "state machine has all four states (presence)")

app_js = (ROOT / "static/js/app.js").read_text(encoding="utf-8", errors="ignore")
A("await greet()" in app_js and "waitWSOpen" in app_js, "Start waits for WS then greet")

# Route linter
import subprocess
p = subprocess.run([sys.executable, str(ROOT/"scripts/route_linter.py")], capture_output=True, text=True, cwd=str(ROOT))
print(p.stdout)
A(p.returncode==0, "route linter passes")

print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
