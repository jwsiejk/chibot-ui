#!/usr/bin/env python3
import os, re, sys

# Banned patterns (v1-only policy)
BANNED = [
    r"/api/greet(?!\w)",               # legacy greet
    r"/api/chat(?![^\w/]|/v1/)",       # /api/chat not under /v1/
    r"orchestrator",                    # legacy 'orchestrator' symbol
]

# Repo root (two levels up from this script)
ROOT = os.path.dirname(os.path.dirname(__file__))

# Exclusions: dirs & specific files we should not scan
EXCLUDE_DIRS = {
    ".venv", "venv", "env", "node_modules", "site-packages", "dist-packages",
    "__pycache__", ".git", "build", "dist", ".eggs",
}
EXCLUDE_FILES = {
    os.path.join("scripts", "route_linter.py"),
    "acceptance_checklist.md",
    "acceptance_checklist_phase7.md",
}

bad_hits = []

for root, dirs, files in os.walk(ROOT):
    # Prune excluded directories in-place
    dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
    for f in files:
        relp = os.path.relpath(os.path.join(root, f), ROOT).replace("\\","/")
        # Skip excluded files
        if any(relp == x or relp.endswith("/" + x) for x in EXCLUDE_FILES):
            continue
        if not f.endswith((".py",".js",".html",".css",".json",".md")):
            continue
        try:
            with open(os.path.join(root, f), "r", encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
            for pat in BANNED:
                for m in re.finditer(pat, text):
                    bad_hits.append((relp, pat, m.group(0)))
        except Exception:
            # Don't let a single read failure tank the linter
            pass

if bad_hits:
    print("ROUTE LINTER: FAIL")
    for p,pat,val in bad_hits:
        print(f"  {p}: matched banned '{pat}' → '{val}'")
    sys.exit(1)
else:
    print("ROUTE LINTER: PASS")
