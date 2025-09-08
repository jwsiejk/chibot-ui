#!/usr/bin/env bash
set -euo pipefail
echo "==> Python version"; python -V
echo "==> Upgrading pip"; python -m pip install --upgrade pip
echo "==> Installing requirements"; pip install -r requirements.txt
echo "==> Running v1-only route linter"; python scripts/route_linter.py
echo "==> CI_FAST=1: running curated checks"; CI_FAST=1 python scripts/phase10_checks.py
echo "==> PROACTIVE: running proactive guard checks"; python scripts/proactive_guard_checks.py
echo "==> Build checks complete"


# Proactive shutdown of any lingering asyncio executors created by tests
python - <<'PY'
import asyncio, sys
try:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    if hasattr(loop, "shutdown_asyncgens"):
        loop.run_until_complete(loop.shutdown_asyncgens())
    if hasattr(loop, "shutdown_default_executor"):
        loop.run_until_complete(loop.shutdown_default_executor())
finally:
    try:
        loop.close()
    except Exception:
        pass
sys.exit(0)
PY

echo "==> Build script exiting 0"
exit 0
