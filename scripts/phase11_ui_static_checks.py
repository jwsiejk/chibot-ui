#!/usr/bin/env python3
import os, re, sys
ROOT = os.path.dirname(os.path.dirname(__file__))
js_dir = os.path.join(ROOT, "static", "js")

def A(c, m):
    print(("PASS" if c else "FAIL") + ": " + m)
    return c

ok = True
if not os.path.isdir(js_dir):
    print("PASS: no static/js directory present (skipping)")  # not a UI build
    sys.exit(0)

# Detect multiple top-level declarations for ensureCSRF/apiPost
decl_pattern = re.compile(r"\b(?:function|const|let|var)\s+(ensureCSRF|apiPost)\b")
counts = {"ensureCSRF":0, "apiPost":0}
where = {"ensureCSRF":[], "apiPost":[]}

for dp, dn, files in os.walk(js_dir):
    for fn in files:
        if not fn.endswith(".js"): continue
        p = os.path.join(dp, fn)
        try:
            txt = open(p, "r", encoding="utf-8").read()
        except Exception:
            continue
        for m in decl_pattern.finditer(txt):
            name = m.group(1)
            counts[name] += 1
            where[name].append(p)

ok &= A(counts["ensureCSRF"] <= 1, f"no duplicate ensureCSRF declarations ({counts['ensureCSRF']})")
if counts["ensureCSRF"] > 1:
    print("   locations:", *where["ensureCSRF"], sep="\n   ")

ok &= A(counts["apiPost"] <= 1, f"no duplicate apiPost declarations ({counts['apiPost']})")
if counts["apiPost"] > 1:
    print("   locations:", *where["apiPost"], sep="\n   ")

sys.exit(0 if ok else 1)
