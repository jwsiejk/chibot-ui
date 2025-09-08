#!/usr/bin/env bash
set -euo pipefail

echo "==> Runtime build (no tests)"
python -V
pip install --upgrade pip
pip install -r requirements.txt

# Lightweight sanity check: ensure ASGI app is importable
python - <<'PY'
import sys
try:
    import app.asgi_gateway as g
    assert hasattr(g, "asgi"), "Missing 'asgi' in app.asgi_gateway"
    print("Sanity import OK: app.asgi_gateway:asgi")
except Exception as e:
    print("Sanity import FAILED:", e)
    sys.exit(1)
PY

echo "==> Build finished cleanly"
exit 0
