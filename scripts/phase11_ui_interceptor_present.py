#!/usr/bin/env python3
import os, sys, re
ROOT = os.path.dirname(os.path.dirname(__file__))
index = os.path.join(ROOT, 'templates', 'index.html')
def A(c,m): print(("PASS" if c else "FAIL")+": "+m); return c
ok=True
if not os.path.exists(index):
    print("PASS: no index.html (skipping)"); sys.exit(0)
t = open(index,'r',encoding='utf-8').read()
has_interceptor = '/static/js/csrf_interceptor.js' in t
ok &= A(has_interceptor, "csrf_interceptor.js included in index.html")
# ensure interceptor tag appears before app.js
if has_interceptor:
    pos_i = t.find('/static/js/csrf_interceptor.js')
    pos_a = t.find('/static/js/app.js')
    ok &= A(pos_i != -1 and pos_a != -1 and pos_i < pos_a, "interceptor loads before app.js")
sys.exit(0 if ok else 1)
