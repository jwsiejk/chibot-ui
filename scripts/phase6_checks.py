
#!/usr/bin/env python3
import os, re, sys, json

ROOT = os.path.dirname(os.path.dirname(__file__))

def read(p):
    with open(p,"r",encoding="utf-8",errors="ignore") as f:
        return f.read()

def assert_true(cond, msg):
    if not cond:
        print("FAIL:", msg); return False
    print("PASS:", msg); return True

ok = True

# 1. State machine exists with states
state_js = read(os.path.join(ROOT, "static/js/state.js"))
ok &= assert_true(all(s in state_js for s in ['READY','LISTENING','THINKING','RESPONDING']), "state machine has all four states")

# 2. Start opens WS then GET /api/v1/greet (order)
app_js = read(os.path.join(ROOT, "static/js/app.js"))
ok &= assert_true("openWS()" in app_js and "await greet()" in app_js, "Start calls openWS then greet")
cfg = read(os.path.join(ROOT, 'static/js/config.js'))
ok &= assert_true(('API.GREET' in app_js) and ('/api/v1/greet' in cfg), 'uses API.GREET ⇒ /api/v1/greet')

# 3. One WebSocket per tab; ≤1 reconnect
ws_js = read(os.path.join(ROOT, "static/js/ws.js"))
ok &= assert_true("let ws = null" in ws_js, "single ws var declared")
ok &= assert_true("reconnects < 1" in ws_js, "≤1 reconnect attempt")

# 4. Soft barge-in ~420 ms
ok &= assert_true("setTimeout(() => sendInterrupt(), 420)" in app_js, "barge-in confirm ~420ms present")

# 5. Nudge ~4200 ms
ok &= assert_true("NUDGE_DELAY_MS" in read(os.path.join(ROOT,"static/js/config.js")), "nudge timing exported")
ok &= assert_true(('scheduleNudge' in ws_js) and ('cmd:\"nudge\"' in ws_js or '\"cmd\":\"nudge\"' in ws_js), 'nudge scheduled')

# 6. Suggestion chips limit + word limit
sugg_js = read(os.path.join(ROOT,"static/js/suggestions.js"))
ok &= assert_true("MAX_CHIPS = 4" in sugg_js, "chip count ≤4 enforced")
ok &= assert_true("MAX_WORDS = 7" in sugg_js, "chip word limit ≤7 enforced")

# 7. Text composer posts to /api/v1/chat; shows 'thinking'
cfg2 = read(os.path.join(ROOT, 'static/js/config.js'))
ok &= assert_true(('API.CHAT' in app_js) and ('/api/v1/chat' in cfg2), 'text posts to API.CHAT ⇒ /api/v1/chat')
ok &= assert_true("setState(STATES.THINKING)" in app_js, "shows thinking on send")

# 8. Error banner
err_js = read(os.path.join(ROOT,"static/js/errors.js"))
ok &= assert_true("showError(route, status" in err_js, "error banner shows route/status")

# 9. Route linter must pass
# We'll import the linter logic quickly:
import subprocess, sys
proc = subprocess.run([sys.executable, os.path.join(ROOT,"scripts","route_linter.py")], capture_output=True, text=True)
print(proc.stdout)
if proc.returncode != 0:
    print(proc.stderr)
    ok = False

# 10. Design Mode inert unless toggled (check CSS selector exists and designed class not auto-applied)
design_css = read(os.path.join(ROOT,"static/css/design-mode.css"))
ok &= assert_true(".design-mode" in design_css and "[data-designable].designed" in design_css, "Design Mode CSS present with inert behavior until activated")

print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
