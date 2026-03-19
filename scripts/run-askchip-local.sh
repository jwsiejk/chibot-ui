#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UI_DIR="$ROOT_DIR/apps/askchip-ui"
API_DIR="$ROOT_DIR/services/askchip-api"

cleanup() {
  if [[ -n "${UI_PID:-}" ]]; then kill "$UI_PID" 2>/dev/null || true; fi
  if [[ -n "${API_PID:-}" ]]; then kill "$API_PID" 2>/dev/null || true; fi
}
trap cleanup EXIT

(
  cd "$API_DIR"
  python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
) &
API_PID=$!

(
  cd "$UI_DIR"
  npm run dev
) &
UI_PID=$!

wait "$API_PID" "$UI_PID"
