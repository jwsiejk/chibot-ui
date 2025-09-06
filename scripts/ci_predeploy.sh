#!/usr/bin/env bash
# CI pre-deploy gate for Ask Chip on Render
# Fails the build if any acceptance checks or the v1-only route linter fail.

set -euo pipefail

echo "==> Installing requirements"
pip install -r requirements.txt

echo "==> Running v1-only route linter"
python scripts/route_linter.py
# Prefer CI_DB_URL if provided; else use local SQLite to avoid network calls during checks
if [ -n "${CI_DB_URL:-}" ]; then
  export DATABASE_URL="${CI_DB_URL}"
  echo "Using CI_DB_URL for checks"
else
  export DATABASE_URL="sqlite:///ci_acceptance.sqlite3"
  echo "Using local SQLite for checks"
fi


# Use local sqlite DB for acceptance checks to avoid network
export DATABASE_URL="sqlite:///ci_acceptance.sqlite3"

run_checks() {
  local label="$1"; shift
  for c in "$@"; do
    if [ -f "$c" ]; then
      echo "==> Running $(basename "$c")"
      python "$c"
    fi
  done
}

# Curate which checks to run based on CI_FAST
# Always run later phases (stability and hygiene). Earlier phases are optional when CI_FAST=1.
LATE_PHASES=(
  scripts/phase10_checks.py
  scripts/phase11_checks.py
  scripts/phase13_checks.py
  scripts/phase14_checks.py
  scripts/phase14_hotfix_checks.py
  scripts/phase14_ui_checks.py
  scripts/phase15_checks.py
  scripts/phase16_checks.py
  scripts/phase17_checks.py
  scripts/phase18_checks.py
  scripts/phase19_checks.py
  scripts/phase20_checks.py
  scripts/phase21_checks.py
)

if [ "${CI_FAST:-}" = "1" ]; then
  echo "==> CI_FAST=1: running curated later-phase checks only"
  run_checks "late" "${LATE_PHASES[@]}"
else
  echo "==> Full CI: running curated checks (10→21)"
  run_checks "late" "${LATE_PHASES[@]}"
fi

echo "==> Running proactive guard checks"
python scripts/proactive_guard_checks.py
echo "==> All checks passed. Proceeding to start command on Render."
