
#!/usr/bin/env python3
import os, re, sys

BANNED = [
    r"/api/greet(?!\w)",               # legacy greet
    r"/api/chat(?![^\w/]|/v1/)",       # /api/chat not under /v1/
    r"orchestrator",                   # legacy orchestrator symbol
]

ROOT = os.path.dirname(os.path.dirname(__file__))
bad_hits = []
IGNORE = {'scripts/route_linter.py','acceptance_checklist.md'}

for root, dirs, files in os.walk(ROOT):
    relroot = os.path.relpath(root, ROOT)
    for f in files:
        relp = os.path.join(relroot, f).replace('\\','/')
        if any(relp.endswith(x) for x in IGNORE):
            continue
        if f.endswith((".py",".js",".html",".css",".json",".md")):
            p = os.path.join(root, f)
            try:
                with open(p,"r",encoding="utf-8",errors="ignore") as fh:
                    text = fh.read()
                for pat in BANNED:
                    for m in re.finditer(pat, text):
                        bad_hits.append((p, pat, m.group(0)))
            except Exception as e:
                pass

if bad_hits:
    print("ROUTE LINTER: FAIL")
    for p,pat,val in bad_hits:
        print(f"  {p}: matched banned '{pat}' → '{val}'")
    sys.exit(1)
else:
    print("ROUTE LINTER: PASS")
