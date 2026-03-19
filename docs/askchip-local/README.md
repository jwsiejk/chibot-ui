# AskChip Local Run Guide

## Localhost defaults
- Frontend dev server: `http://127.0.0.1:5173`
- Backend API and WebSocket host: `http://127.0.0.1:8000` and `ws://127.0.0.1:8000`

## Frontend runtime configuration
The AskChip frontend is local-first and defaults to localhost when no overrides are provided.

- `VITE_ASKCHIP_API_BASE_URL` defaults to `http://127.0.0.1:8000`
- `VITE_ASKCHIP_WS_BASE_URL` defaults to `ws://127.0.0.1:8000`
- API requests resolve against `${VITE_ASKCHIP_API_BASE_URL}/api/v1/...`
- Typed-chat event streaming resolves against `${VITE_ASKCHIP_WS_BASE_URL}/ws/events`
- Canonical WebRTC signaling resolves against `${VITE_ASKCHIP_WS_BASE_URL}/ws/webrtc`
- `POST /api/v1/webrtc/offer` remains compatibility-only and is not the primary signaling path

## Current frontend scope
- Typed chat is implemented, including transcript loading, session selection, and streaming assistant text updates.
- WebRTC foundation work is implemented for mic readiness, peer negotiation, and transport diagnostics only.
- Voice/WebRTC conversation is not implemented in this phase.
- STT, TTS, voice controls, tools, RAG, and auth remain out of scope.

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
