# AskChip Local Run Guide

## Windows (PowerShell)
1. Create and activate a Python 3.11+ virtual environment in `services/askchip-api`.
2. Install API dependencies: `pip install -e .[dev]`
3. Install UI dependencies in `apps/askchip-ui`: `npm install`
4. Start both services from the repo root:
   ```powershell
   ./scripts/run-askchip-local.ps1
   ```
5. Open the UI at `http://127.0.0.1:5173`.

## Bash
1. Create and activate a Python 3.11+ virtual environment in `services/askchip-api`.
2. Install API dependencies: `pip install -e .[dev]`
3. Install UI dependencies in `apps/askchip-ui`: `npm install`
4. Start both services from the repo root:
   ```bash
   ./scripts/run-askchip-local.sh
   ```
5. Open the UI at `http://127.0.0.1:5173`.

## Ports
- UI: `127.0.0.1:5173`
- API: `127.0.0.1:8000`
