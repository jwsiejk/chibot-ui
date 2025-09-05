#!/usr/bin/env python3
import os, sys, re, json, glob

ROOT = "."

def assert_true(cond, msg):
    if not cond:
        print("FAIL:", msg); return False
    print("PASS:", msg); return True

ok = True

# Ensure ASGI app entrypoint exists
ok &= assert_true(os.path.exists("app/asgi_gateway.py"), "ASGI gateway present")

# Ensure index template
ok &= assert_true(os.path.exists("templates/index.html"), "index.html present")

# Ensure no obvious secrets committed
bad = []
for p in glob.glob("app/**/*.py", recursive=True):
    txt = open(p, "r", encoding="utf-8", errors="ignore").read()
    if "sk_live_" in txt or "aws_secret_access_key" in txt:
        bad.append(p)
ok &= assert_true(not bad, "No obvious secrets in source")

print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
