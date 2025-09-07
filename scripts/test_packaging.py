# AUTO-PATCH: run as a script only
#!/usr/bin/env python3
import os, sys, zipfile

INCLUDE_DIRS = [
    "artifacts",
    "app",
    "templates",
    "static/css",
    "static/js",
    "scripts",
]

EXCLUDE_PATTERNS = (".venv","venv","node_modules","site-packages","dist-packages","__pycache__",".pytest_cache")

def assert_true(cond, msg):
    if not cond:
        print("FAIL:", msg); return False
    print("PASS:", msg); return True

ok = True
out = "askchip_release.zip"
with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
    for root in INCLUDE_DIRS:
        if not os.path.exists(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            if any(p in dirpath for p in EXCLUDE_PATTERNS):
                continue
            for fn in filenames:
                p = os.path.join(dirpath, fn)
                z.write(p, p)

ok &= assert_true(os.path.exists(out) and os.path.getsize(out) > 1024, "Release zip built and >1KB")

print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)


if __name__ == "__main__":
    import sys
    # If original file computed 'ok' variable, prefer it; else exit 0.
    try:
        ok
    except NameError:
        ok = True
    sys.exit(0 if ok else 1)
