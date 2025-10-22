#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if command -v python3 >/dev/null 2>&1; then
  PY="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PY="$(command -v python)"
else
  echo "BUILD_01_TESTS: FAIL" >&2
  echo "python interpreter not found" >&2
  exit 1
fi

TEST_PUBLISH="$ROOT_DIR/tests/test_bus_publish_basics.py"
TEST_REDACTION="$ROOT_DIR/tests/test_bus_redaction.py"

if [[ ! -f "$TEST_PUBLISH" || ! -f "$TEST_REDACTION" ]]; then
  echo "BUILD_01_TESTS: FAIL" >&2
  echo "required test files are missing" >&2
  exit 1
fi

if "$PY" -m unittest -v tests.test_bus_publish_basics tests.test_bus_redaction; then
  echo "BUILD_01_TESTS: PASS"
else
  status=$?
  echo "BUILD_01_TESTS: FAIL"
  exit "$status"
fi
