#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if command -v python3 >/dev/null 2>&1; then
  PY="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PY="$(command -v python)"
else
  echo "BUILD_02_TESTS: FAIL" >&2
  echo "python interpreter not found" >&2
  exit 1
fi

MODULES=(
  tests.test_policy_loader
  tests.test_policy_apply_and_diff
  tests.test_acwr_breadcrumb
)

missing_files=()
for module in "${MODULES[@]}"; do
  module_path="${module//.//}.py"
  if [[ ! -f "$ROOT_DIR/$module_path" ]]; then
    missing_files+=("$module_path")
  fi
done

if [[ ${#missing_files[@]} -gt 0 ]]; then
  echo "BUILD_02_TESTS: FAIL" >&2
  echo "required test files are missing: ${missing_files[*]}" >&2
  exit 1
fi

if "$PY" -m unittest -v "${MODULES[@]}"; then
  echo "BUILD_02_TESTS: PASS"
else
  status=$?
  echo "BUILD_02_TESTS: FAIL"
  exit "$status"
fi
