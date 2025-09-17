
import os, re, json, sys

ROOT = os.path.dirname(os.path.dirname(__file__))
failures = []

def find_files(exts=(".py", ".html", ".js")):
    for dirpath, dirnames, filenames in os.walk(ROOT):
        if any(part in dirpath for part in ("tests", "tests_phase3", "tests_ui_fix", "tests_da_alltests", "docs")):
            continue
        # Skip virtualenvs or build/cache dirs
        if any(skip in dirpath for skip in (".venv", "venv", "__pycache__", "node_modules", "dist", "build")):
            continue
        for fn in filenames:
            if fn.endswith(exts):
                yield os.path.join(dirpath, fn)

# Test 1: WS-only guard — ensure WS route exists and legacy endpoints do not exist
ws_found = False
for path in find_files((".py", ".js", ".ts")):
    if path.endswith("route_linter.py"): continue
    try:
        txt = open(path, "r", encoding="utf-8", errors="ignore").read()
    except Exception:
        continue
    if '/ws/v1/chat' in txt:
        ws_found = True
    for legacy in ['/api/v1/voice/chunk', '/api/v1/voice/end', '/api/greet']:
        if legacy in txt:
            failures.append(f"Legacy route present: {legacy} in {path}")

if not ws_found:
    failures.append("WS route not referenced anywhere: /ws/v1/chat")

# Test 2: TTS route should exist (HTTP allowed per guardrails)
tts_found = False
for path in find_files((".py", ".js", ".ts")):
    if path.endswith("route_linter.py"): continue
    try:
        txt = open(path, "r", encoding="utf-8", errors="ignore").read()
    except Exception:
        continue
    if '/api/v1/voice/tts-with-visemes' in txt:
        tts_found = True
        break
if not tts_found:
    failures.append("TTS route reference missing: /api/v1/voice/tts-with-visemes")

# Test 3: Origin check middleware present (basic pattern check)
origin_check_present = False
for path in find_files((".py",)):
    try:
        txt = open(path, "r", encoding="utf-8", errors="ignore").read()
    except Exception:
        continue
    if re.search(r"class\s+OriginCheckMiddleware|def\s+origin_check_middleware", txt):
        origin_check_present = True
        break
if not origin_check_present:
    failures.append("Origin check middleware not found (Phase 3 security).")

# Test 4: WS KeepAlive handling (search for KeepAlive handling in server code)
keepalive_present = False
for path in find_files((".py",)):
    try:
        txt = open(path, "r", encoding="utf-8", errors="ignore").read()
    except Exception:
        continue
    if re.search(r'\"type\":\s*\"KeepAlive\"|type\s*==\s*\"KeepAlive\"', txt):
        keepalive_present = True
        break
if not keepalive_present:
    failures.append("WS KeepAlive handling not found (Phase 3).")

# Test 5: CloseStream handling (end-of-turn) — must be recognized serverside
closestream_present = False
for path in find_files((".py",)):
    try:
        txt = open(path, "r", encoding="utf-8", errors="ignore").read()
    except Exception:
        continue
    if re.search(r'\"type\":\s*\"CloseStream\"|type\s*==\s*\"CloseStream\"', txt):
        closestream_present = True
        break
if not closestream_present:
    failures.append("CloseStream handling not found (Phase 3).")

# Test 6: UtteranceEnd optional signal passthrough (pattern)
utt_end_present = False
for path in find_files((".py",)):
    try:
        txt = open(path, "r", encoding="utf-8", errors="ignore").read()
    except Exception:
        continue
    if re.search(r'\"type\":\s*\"UtteranceEnd\"|type\s*==\s*\"UtteranceEnd\"', txt):
        utt_end_present = True
        break
if not utt_end_present:
    failures.append("UtteranceEnd handling/passthrough not found (Phase 3).")

# Test 7: PII redaction helper present (basic function name check)
pii_redact_present = False
for path in find_files((".py",)):
    try:
        txt = open(path, "r", encoding="utf-8", errors="ignore").read()
    except Exception:
        continue
    if re.search(r"def\s+redact_pii\(", txt):
        pii_redact_present = True
        break
if not pii_redact_present:
    failures.append("PII redaction helper not found (Phase 3).")

# Test 8: Usage caps present (rate limit or token/time caps — config present)
usage_caps_present = False
for path in find_files((".py", ".md", ".yaml", ".yml")):
    try:
        txt = open(path, "r", encoding="utf-8", errors="ignore").read()
    except Exception:
        continue
    if re.search(r"RATE_LIMIT|USAGE_CAP|MAX_TURN_SEC|MAX_SESSION_MIN|rate_limit", txt, re.I):
        usage_caps_present = True
        break
if not usage_caps_present:
    failures.append("Usage caps not found (Phase 3).")

# Test 9: Route-linter script exists and fails on legacy routes
linter_path = os.path.join(ROOT, "scripts", "route_linter.py")
if not os.path.exists(linter_path):
    failures.append("Route-linter script missing at scripts/route_linter.py")
else:
    # run linter, expect zero exit code
    import runpy
    try:
        runpy.run_path(linter_path, run_name="__main__")
    except SystemExit as e:
        code = int(getattr(e, "code", 0) or 0)
        if code != 0:
            failures.append(f"Route-linter failed with code {code} (should only fail when forbidden routes are present).")

# Summary
if failures:
    print("\nPHASE 3 TESTS: FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
else:
    print("\nPHASE 3 TESTS: PASS")
