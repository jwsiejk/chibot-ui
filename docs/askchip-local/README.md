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
- WebRTC foundation work is implemented for mic readiness, peer negotiation, explicit disconnect cleanup, and transport diagnostics only.
- Backend WebRTC peer/session lifetime is intentionally not tied to the signaling WebSocket lifetime; explicit disconnect and backend orphan cleanup release peer sessions.
- Push-to-talk voice input is implemented through direct microphone capture plus backend faster-whisper transcription after release.
- WebRTC remains foundation-only for diagnostics/signaling and is not required for voice-turn capture or upload.
- Phase 6 adds local Kokoro assistant speech for completed assistant messages, with explicit interrupt-on-submit / interrupt-on-PTT while speech is actually playing.
- Wake word, always-open microphones, tools, RAG, and auth remain out of scope.

## Windows (PowerShell)
1. Create and activate a Python 3.11+ virtual environment in `services/askchip-api`.
2. Install API dependencies: `pip install -e .[dev]`
   - Voice input depends on `faster-whisper` plus its local runtime prerequisites. On Windows 11 local-first setups, keep the configured model/device/compute settings aligned with your machine capabilities.
   - Assistant speech now depends on local Kokoro runtime support via `kokoro-onnx`. If Kokoro model/voice assets are not available locally, typed chat and transcript completion still work but speech synthesis requests will fail honestly.
3. Install UI dependencies in `apps/askchip-ui`: `npm install`
4. Start both services from the repo root:
   ```powershell
   ./scripts/run-askchip-local.ps1
   ```
5. Open the UI at `http://127.0.0.1:5173`.

## Bash
1. Create and activate a Python 3.11+ virtual environment in `services/askchip-api`.
2. Install API dependencies: `pip install -e .[dev]`
   - Voice input depends on `faster-whisper` plus its local runtime prerequisites. On Windows 11 local-first setups, keep the configured model/device/compute settings aligned with your machine capabilities.
   - Assistant speech now depends on local Kokoro runtime support via `kokoro-onnx`. If Kokoro model/voice assets are not available locally, typed chat and transcript completion still work but speech synthesis requests will fail honestly.
3. Install UI dependencies in `apps/askchip-ui`: `npm install`
4. Start both services from the repo root:
   ```bash
   ./scripts/run-askchip-local.sh
   ```
5. Open the UI at `http://127.0.0.1:5173`.

## Phase 6 speech configuration
- `ASKCHIP_TTS_VOICE` defaults to `af_heart`.
- `ASKCHIP_TTS_DEVICE` defaults to `cpu`.
- `ASKCHIP_TTS_MODEL_PATH` and `ASKCHIP_TTS_VOICES_PATH` can point at local Kokoro assets when your runtime requires explicit paths.
- `ASKCHIP_TTS_SAMPLE_RATE_HZ`, `ASKCHIP_TTS_SPEED`, and `ASKCHIP_TTS_LANG_CODE` tune local speech synthesis.
- Assistant speech is fetched from a dedicated HTTP endpoint, then the frontend reports real playback start/stop so `speaking` only appears while audio is actually playing.
- Typed submit and push-to-talk press explicitly stop active assistant playback before the next turn starts. Merely typing in the composer does not interrupt playback.
