#!/usr/bin/env bash
set -euo pipefail
# Nightly staging smoke for Ask Chip
# Expects real vendor keys in environment on the staging service.
python3 scripts/scenario_voice_loop.py | tee -a nightly_staging.log
