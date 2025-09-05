#!/usr/bin/env bash
# CI pre-deploy gate for Ask Chip on Render
# Fails the build if any acceptance checks or the v1-only route linter fail.

set -euo pipefail

echo "==> Installing requirements"
pip install -r requirements.txt

echo "==> Preflight (must pass)"
python scripts/preflight.py
python scripts/verify_preflight.py

echo "==> Running v1-only route linter"
python scripts/route_linter.py

echo "==> Phase 6 checks"
python scripts/phase6_checks.py

echo "==> Phase 7 checks"
python scripts/phase7_checks.py

echo "==> Phase 7.1 checks (provider wiring)"
python scripts/phase7_1_checks.py

echo "==> Phase 8 checks (retrieval/persona prompt)"
python scripts/phase8_checks.py

echo "==> Phase 9 checks (audio playback + Admin Knowledge UI)"
python scripts/phase9_checks.py

echo "==> All checks passed. Proceeding to start command on Render."
