#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="${PYTHONPATH:-.}:."
PY="${PYTHON:=python3}"; command -v "$PY" >/dev/null 2>&1 || PY=python
"$PY" -m unittest -v \
  tests.test_gate_controller \
  tests.test_tts_mask_lifecycle \
  tests.test_turn_state_machine \
  tests.test_auto_barge_decision
echo "BUILD_04_TESTS: PASS"
