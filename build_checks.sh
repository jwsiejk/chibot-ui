#!/usr/bin/env bash
set -euo pipefail
export CI_FAST=${CI_FAST:-1}
export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1

echo "==> Installing test deps"
pip install --upgrade pip
pip install -r requirements.txt
# ensure pytest + timeout available
python - <<'PY'
import sys, subprocess
def pip_install(pkg):
    subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])
for pkg in ("pytest", "pytest-timeout"):
    try:
        __import__(pkg.split('==')[0])
    except Exception:
        pip_install(pkg)
print("Test deps ready")
PY

echo "==> Running curated checks"
# Respect local pytest.ini addopts (timeout/maxfail)
pytest -q || TEST_STATUS=$?

TEST_STATUS=${TEST_STATUS:-0}

echo "==> Async/Thread cleanup to prevent Render 143 timeouts"
python - <<'PY'
import asyncio, threading, sys
# mark any leftover non-daemon threads as daemon so interpreter can exit
for t in threading.enumerate():
    if t is threading.current_thread():
        continue
    try:
        t.daemon = True
    except Exception:
        pass

# new loop to shutdown async gens/executor cleanly
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
try:
    if hasattr(loop, "shutdown_asyncgens"):
        loop.run_until_complete(loop.shutdown_asyncgens())
    if hasattr(loop, "shutdown_default_executor"):
        try:
            loop.run_until_complete(loop.shutdown_default_executor())
        except TypeError:
            # py<3.9 doesn't support coroutine form
            pass
finally:
    loop.close()
print("Cleanup complete.")
PY

if [ "$TEST_STATUS" != "0" ]; then
  echo "==> Tests failed ($TEST_STATUS)"
  exit $TEST_STATUS
fi

echo "==> Tests passed. Exiting cleanly..."
exit 0
