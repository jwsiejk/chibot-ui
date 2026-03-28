# AskChip Local Run Guide

## Contract artifact
- The authoritative, reviewable AskChip Local v1 contract now lives in the repo root at `AskChip Local v1 Contract.md`.
- The legacy `AskChip Local v1 Contract.docx` remains as an export artifact, but pull-request contract updates should be made in the markdown file.

## Localhost defaults
- Frontend dev server: `http://127.0.0.1:5173`
- Backend API and WebSocket host: `http://127.0.0.1:8000` and `ws://127.0.0.1:8000`
- Local API development expects CORS middleware to allow `http://127.0.0.1:5173` and `http://localhost:5173`.

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
- Phase 6 adds local Kokoro assistant speech from the same canonical assistant message, now starting as soon as a stable sentence-level chunk is available while generation is still streaming.
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
- `ASKCHIP_STT_DEVICE` and `ASKCHIP_STT_COMPUTE_TYPE` explicitly control faster-whisper runtime execution. When `ASKCHIP_STT_DEVICE=auto`, startup diagnostics now report the actual selected execution device.
- When using the espeak fallback backend, American English voices should use `en-us` (British English would use `en-gb`).
- Runtime startup diagnostics now report the selected STT device/compute type and Kokoro ONNX provider/device, including explicit warnings when a requested GPU path is unavailable and the runtime falls back to CPU.
- Assistant speech is fetched from a dedicated HTTP endpoint, then the frontend reports real playback start/stop so `speaking` only appears while audio is actually playing.
- Speech no longer waits for a fully completed assistant message before the first audio starts; the frontend may request stable sentence-level chunks from the same canonical assistant message while generation is still in progress.
- Completed turns now emit a compact `turn.latency` diagnostic event (correlated by `trace_id` when provided), and recent per-turn latency summaries are visible in the diagnostics drawer for local inspection.
- Canonical transcript storage remains unified and unchanged: `text` is still the source of truth, `role` is speaker identity, `source` is origin semantics, and there is no alternate frontend-only message shape.
- If a spoken chunk ends before generation has produced the next stable sentence, session state may return from `speaking` to `thinking` until the next chunk is ready. Once generation and playback are both complete, state returns to `ready`.
- Typed submit and push-to-talk press explicitly stop active assistant playback before the next turn starts. Merely typing in the composer does not interrupt playback.
- This still uses plain-text Kokoro TTS only. It does not add SSML or injected laugh/chuckle audio clips, and it does not increase `ASKCHIP_TTS_SPEED`.
