#!/usr/bin/env python3
import os, re, sys
ROOT = os.path.dirname(os.path.dirname(__file__))
js_dir = os.path.join(ROOT, "static", "js")

def A(c, m):
    print(("PASS" if c else "FAIL") + ": " + m)
    return c

ok = True
if not os.path.isdir(js_dir):
    print("PASS: no static/js directory present (skipping)")
    sys.exit(0)

bad = []
pat = re.compile(r"fetch\(\s*['\"]/api/v1/chat['\"][^)]*\)")
for dp, _, files in os.walk(js_dir):
    for fn in files:
        if not fn.endswith(".js"):
            continue
        p = os.path.join(dp, fn)
        try:
            txt = open(p, "r", encoding="utf-8").read()
        except Exception:
            continue
        for m in pat.finditer(txt):
            # If the same file references apiPost, consider it ok
            if "apiPost(" in txt or "window.apiPost" in txt:
                continue
            bad.append(p)

ok &= A(len(bad) == 0, "no raw fetch('/api/v1/chat') without apiPost helper")
if bad:
    print("   offending files:", *sorted(set(bad)), sep="\n   ")
sys.exit(0 if ok else 1)
