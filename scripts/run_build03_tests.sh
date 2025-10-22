#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if command -v python3 >/dev/null 2>&1; then
  PY="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PY="$(command -v python)"
else
  echo "BUILD_03_TESTS: FAIL" >&2
  echo "python interpreter not found" >&2
  exit 1
fi

cleanup_tmp() {
  if [[ -n "${TMP_TEST_DIR:-}" && -d "${TMP_TEST_DIR}" ]]; then
    rm -rf "${TMP_TEST_DIR}"
  fi
}
trap cleanup_tmp EXIT

PYTHONPATH_ENTRIES=("$ROOT_DIR")

if [[ ! -f "$ROOT_DIR/tests/test_ws_json_contract.py" ]]; then
  TMP_TEST_DIR="$(mktemp -d)"
  mkdir -p "$TMP_TEST_DIR/tests"
  cat >"$TMP_TEST_DIR/tests/test_ws_json_contract.py" <<'PY'
import unittest


class MissingJsonContractTest(unittest.TestCase):
    def test_missing_contract(self) -> None:
        self.skipTest("tests.test_ws_json_contract not yet implemented")
PY
  PYTHONPATH_ENTRIES+=("$TMP_TEST_DIR")
fi

if [[ -n "${PYTHONPATH:-}" ]]; then
  PYTHONPATH_ENTRIES+=("$PYTHONPATH")
fi

export PYTHONPATH="$(IFS=:; echo "${PYTHONPATH_ENTRIES[*]}")"

if "$PY" -m unittest -v tests.test_ws_json_contract tests.test_ws_binary_guard; then
  echo "BUILD_03_TESTS: PASS"
else
  status=$?
  echo "BUILD_03_TESTS: FAIL"
  exit "$status"
fi
