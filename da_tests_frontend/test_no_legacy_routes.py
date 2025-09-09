
import os, re, pathlib

IGNORE_DIRS = {"tests", "da_tests", "da_tests2", "da_tests3", "_da_alltests", "acceptance", "phase_tests", "scripts"}
IGNORE_FILES = {"acceptance_checklist.md", "acceptance_checklist_phase7.md", "acceptance_checklist_phase8.md", "route_linter.py"}

def test_no_legacy_routes():
    bad = []
    root = "/opt/project" if os.path.exists("/opt/project") else "/mnt/data/workspace"
    for dirpath, _, files in os.walk(root):
        parts = set(os.path.normpath(dirpath).split(os.sep))
        if parts & IGNORE_DIRS:
            continue
        for fn in files:
            if fn in IGNORE_FILES: 
                continue
            if fn.endswith((".py",".js",".html",".md",".txt",".ini",".cfg",".json")):
                p = os.path.join(dirpath, fn)
                try:
                    with open(p, "r", encoding="utf-8", errors="ignore") as f:
                        txt=f.read()
                except:
                    continue
                if re.search(r"/api/(?!v1/)(greet|chat|voice|admin|auth)\b", txt):
                    bad.append(p)
    assert not bad, "Legacy non-v1 routes found: " + ", ".join(sorted(set(bad)))
