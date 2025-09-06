#!/usr/bin/env python3
import os, sys, subprocess

SCRIPTS_DIR = os.path.dirname(__file__)
ROOT = os.path.dirname(SCRIPTS_DIR)

def run(label, cmd, cwd=None):
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd or ROOT)
    print(f"== {label} ==")
    print(p.stdout)
    if p.stderr: print(p.stderr)
    return p.returncode

rc = 0
os.environ.setdefault("DATABASE_URL", "sqlite:///ci_acceptance.sqlite3")
for name in [
    "phase10_checks.py",
    "phase11_checks.py",
    "phase13_checks.py",
    "phase14_checks.py",
    "phase14_hotfix_checks.py",
    "phase14_ui_checks.py",
    "phase15_checks.py",
    "phase16_checks.py",
    "phase17_checks.py",
    "phase18_checks.py",
    "phase19_checks.py",
    "phase20_checks.py",
    "phase21_checks.py",
]:
    p = os.path.join(ROOT, "scripts", name)
    if os.path.exists(p):
        rc |= run(name, [sys.executable, p])

print("\nPRELIGHT RESULT:", "PASS" if rc == 0 else "FAIL")
sys.exit(rc)
