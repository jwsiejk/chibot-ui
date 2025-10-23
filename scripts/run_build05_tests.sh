#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="${PYTHONPATH:-.}:."
PY="${PYTHON:=python3}"; command -v "$PY" >/dev/null 2>&1 || PY=python
"$PY" -m unittest -v \
  tests.test_asr_readiness_gate \
  tests.test_asr_adapter_basic \
  tests.test_llm_stub \
  tests.test_dual_vad_arbiter \
  tests.test_tts_stub
"$PY" -m unittest -v tests.test_nlu_once_per_turn
"$PY" -m unittest -v tests.test_policy_nlg_once_per_turn
echo "BUILD_05_TESTS: PASS"
