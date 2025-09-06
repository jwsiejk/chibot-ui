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
rc |= run("phase6", [sys.executable, os.path.join(ROOT,"scripts","phase6_checks.py")])
rc |= run("phase7", [sys.executable, os.path.join(ROOT,"scripts","phase7_checks.py")])
rc |= run("phase7_1", [sys.executable, os.path.join(ROOT,"scripts","phase7_1_checks.py")])
rc |= run("phase8", [sys.executable, os.path.join(ROOT,"scripts","phase8_checks.py")])
rc |= run("our_pytests", [sys.executable, "-m", "pytest", "-q", "_chip_checks"], cwd=ROOT)

print("\nPRELIGHT RESULT:", "PASS" if rc == 0 else "FAIL")
sys.exit(rc)
