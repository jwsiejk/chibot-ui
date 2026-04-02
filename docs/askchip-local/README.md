# AskChip Local Run Guide

## Contract artifact
- The authoritative, reviewable AskChip Local v1 contract now lives in the repo root at `AskChip Local v1 Contract.md`.
- The legacy `AskChip Local v1 Contract.docx` remains as an export artifact, but pull-request contract updates should be made in the markdown file.
- Expert Desk frontstage progress is tracked in `docs/askchip-local/expert-desk-demo.md`.

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


## Ollama model defaults
- AskChip Local now defaults to `OLLAMA_MODEL=gemma3:4b` for local generation.
- Pull the default model locally before starting the API:
  ```bash
  ollama pull gemma3:4b
  ```
- AskChip Local also sets explicit Ollama runtime request defaults for local responsiveness:
  - `OLLAMA_KEEP_ALIVE=30m`
  - `OLLAMA_NUM_CTX=8192`
  - `OLLAMA_NUM_PARALLEL=1`
- `OLLAMA_NUM_PARALLEL=1` is intentional for this local, voice-first assistant profile: it keeps memory pressure predictable so streaming chat + STT/TTS stay responsive on a single machine.
- Ollama memory use scales with effective parallelism × context length. Increasing `OLLAMA_NUM_PARALLEL` and/or `OLLAMA_NUM_CTX` raises peak memory requirements.
- `OLLAMA_NUM_PARALLEL` is an Ollama server/runtime setting. It is not a per-request `/api/chat` payload field in AskChip.
- Verify where Gemma is loaded (CPU/GPU split) with:
  ```bash
  ollama ps
  ```
- You can still override the model without code changes by setting `OLLAMA_MODEL` in your shell/environment before starting the API.
- `/api/v1/config` reports the active backend model and resolved Ollama runtime settings (`ollama_model`, `ollama_keep_alive`, `ollama_num_ctx`, `ollama_num_parallel`), plus requested/selected STT device and compute type.
- `/api/v1/readiness` always performs a local installed-model check for the configured `OLLAMA_MODEL`, even when `ASKCHIP_OLLAMA_WARMUP_ENABLED=false`; warm-up requests remain disabled in that mode.

## Current frontend scope
- Typed chat is implemented, including transcript loading, session selection, and streaming assistant text updates.
- WebRTC foundation work is implemented for mic readiness, peer negotiation, explicit disconnect cleanup, and transport diagnostics only.
- Backend WebRTC peer/session lifetime is intentionally not tied to the signaling WebSocket lifetime; explicit disconnect and backend orphan cleanup release peer sessions.
- Push-to-talk voice input is implemented through direct microphone capture plus backend faster-whisper transcription after release.
- WebRTC remains foundation-only for diagnostics/signaling and is not required for voice-turn capture or upload.
- Phase 6 adds local Kokoro assistant speech from the same canonical assistant message, now starting as soon as a stable sentence-level chunk is available while generation is still streaming.
- Wake word, always-open microphones, tools, RAG, and auth remain out of scope.

## Windows (PowerShell)
1. Run the dedicated Windows/NVIDIA setup flow from the repo root:
   ```powershell
   ./scripts/setup-askchip-local-windows-nvidia.ps1
   ```
   This script:
   - creates/uses `services/askchip-api/.venv`
   - installs backend deps
   - replaces CPU `onnxruntime` with `onnxruntime-gpu`
   - verifies `CUDAExecutionProvider` is actually available
2. Activate backend `.venv`:
   ```powershell
   .\services\askchip-api\.venv\Scripts\Activate.ps1
   ```
3. Install UI dependencies in `apps/askchip-ui`: `npm install`
4. Start both services from the repo root:
   ```powershell
   ./scripts/run-askchip-local.ps1
   ```
   > `run-askchip-local.ps1` now requires `services/askchip-api/.venv` and uses that interpreter directly to prevent accidental global-environment CPU fallback.
5. Open the UI at `http://127.0.0.1:5173`.

## Bash
1. Create and activate a dedicated Python 3.11+ virtual environment in `services/askchip-api/.venv`.
2. Install API dependencies: `pip install -e .[dev]`
   - Voice input depends on `faster-whisper` plus its local runtime prerequisites. On Windows 11 local-first setups, keep the configured model/device/compute settings aligned with your machine capabilities.
   - Assistant speech now depends on local Kokoro runtime support via `kokoro-onnx`. If Kokoro model/voice assets are not available locally, typed chat and transcript completion still work but speech synthesis requests will fail honestly.
3. Install UI dependencies in `apps/askchip-ui`: `npm install`
4. Start both services from the repo root:
   ```bash
   ./scripts/run-askchip-local.sh
   ```
   > `run-askchip-local.sh` now requires `services/askchip-api/.venv` and uses that interpreter directly.
5. Open the UI at `http://127.0.0.1:5173`.

## Phase 6 speech configuration
- `ASKCHIP_TTS_VOICE` defaults to `am_echo`.
- `ASKCHIP_TTS_DEVICE` defaults to `auto`.
- `ASKCHIP_TTS_MODEL_PATH` and `ASKCHIP_TTS_VOICES_PATH` can point at local Kokoro assets when your runtime requires explicit paths.
- `ASKCHIP_TTS_SAMPLE_RATE_HZ`, `ASKCHIP_TTS_SPEED`, and `ASKCHIP_TTS_LANG_CODE` tune local speech synthesis.
- `ASKCHIP_STT_DEVICE` and `ASKCHIP_STT_COMPUTE_TYPE` explicitly control faster-whisper runtime execution.
  - `ASKCHIP_STT_DEVICE=auto` resolves to CUDA when available, otherwise CPU.
  - `ASKCHIP_STT_COMPUTE_TYPE=auto` resolves to `int8_float16` on CUDA and `int8` on CPU.
- GPU-enabled STT expectations:
  - If CUDA is not actually selected by faster-whisper at runtime, diagnostics honestly report CPU selection.
  - `/api/v1/config` surfaces the selected STT device and resolved compute type.
- GPU-enabled Kokoro expectations:
  - `ASKCHIP_TTS_DEVICE=auto` chooses CUDA only when ONNX Runtime reports `CUDAExecutionProvider`; otherwise it falls back to CPU.
  - On Windows, when `onnxruntime-gpu` exposes CUDA support, AskChip preloads the ONNX Runtime CUDA DLLs before session initialization.
  - If CUDA is unavailable (including `ASKCHIP_TTS_DEVICE=auto`), runtime diagnostics include an explicit warning/fallback reason and show CPU selection honestly.
  - `services/askchip-api/pyproject.toml` includes an optional `gpu-tts` extra for explicit GPU runtime installs (`pip install -e .[dev,gpu-tts]`), but environment/provider availability must still be verified at runtime.
- Verify ONNX Runtime provider availability from the backend `.venv`:
  ```bash
  python -c "import onnxruntime as ort; print(ort.get_available_providers())"
  ```
- Verify backend runtime diagnostics:
  ```bash
  curl http://127.0.0.1:8000/api/v1/config
  curl http://127.0.0.1:8000/api/v1/readiness
  ```
- GPU success criteria:
  - `/api/v1/config` shows `tts_device = cuda`
  - `/api/v1/config` shows `tts_provider = CUDAExecutionProvider`
- Honest fallback criteria:
  - `/api/v1/config` shows `tts_device = cpu`
  - `/api/v1/config` includes `tts_warning` / `tts_fallback_reason` explaining why CUDA was unavailable
- When using the espeak fallback backend, American English voices should use `en-us` (British English would use `en-gb`).
- Runtime startup diagnostics now report the selected STT device/compute type and Kokoro ONNX provider/device, including explicit warnings when a requested GPU path is unavailable and the runtime falls back to CPU.
- Assistant speech is fetched from a dedicated HTTP endpoint, then the frontend reports real playback start/stop so `speaking` only appears while audio is actually playing.
- Speech no longer waits for a fully completed assistant message before the first audio starts; the frontend may request stable sentence-level chunks from the same canonical assistant message while generation is still in progress.
- Completed turns now emit a compact `turn.latency` diagnostic event (correlated by `trace_id` when provided), and recent per-turn latency summaries are visible in the diagnostics drawer for local inspection.
- Canonical transcript storage remains unified and unchanged: `text` is still the source of truth, `role` is speaker identity, `source` is origin semantics, and there is no alternate frontend-only message shape.
- If a spoken chunk ends before generation has produced the next stable sentence, session state may return from `speaking` to `thinking` until the next chunk is ready. Once generation and playback are both complete, state returns to `ready`.
- Typed submit and push-to-talk press explicitly stop active assistant playback before the next turn starts. Merely typing in the composer does not interrupt playback.
- This still uses plain-text Kokoro TTS only. It does not add SSML or injected laugh/chuckle audio clips, and it does not increase `ASKCHIP_TTS_SPEED`.
- Visual Session is now in an interview-ready polish pass with improved header/stage/toolbar/drawer treatment while retaining the existing shared runtime and chat architecture.
- Visual Session bootstrap now fails fast for invalid/deleted `/visual-session/:sessionId` routes and shows a terminal unavailable state instead of hanging on "Loading session context…".
- Bootstrap dependency reads (`/config`, `/readiness`, `/sessions`, transcript load) now use frontend request timeouts and surface explicit dependency-specific errors to the user.
- Frontstage Expert Desk demo routes are available at `/demo` (landing), `/demo/intake` (structured intake persisted in frontend `sessionStorage` only), and `/demo/recommendation` (deterministic recommendation/routing with a real live-session launch CTA).
- Frontstage recommendation launch now sends an optional typed `metadata.expert_desk` payload during session creation so Expert Desk intake/recommendation context is persisted on the backend session record before the first live turn.
- Frontstage recommendation now hands off canonical expert persona routing fields (`expert_persona_id`, `expert_persona_label`, optional `expert_persona_summary`) end-to-end so backend live runtime applies the intended specialist overlay deterministically.
- Live typed and voice turn prompting now reads backend session-scoped `metadata.expert_desk` and prepends expert persona plus intake pre-brief context as prompt system context before transcript history/current user turn; canonical transcript storage remains unchanged.
- VMware Expert Desk runtime now includes uploaded-log receipt awareness in backend `metadata.expert_desk` (`uploaded_logs_count`, `uploaded_log_names`, `uploaded_logs_available`, optional `recommended_vmware_logs`) so first and follow-up VMware responses can honestly acknowledge whether logs were received.
- VMware AI expert live-conversation tuning now makes the first live response a short conversational opener (typically 2-3 sentences, no playbook headings/checklists by default), explicitly acknowledges log receipt vs no-log state, and asks one focused next question; follow-up turns stay short and engineer-like with grounded likely paths and practical verification while preserving explicit honesty that uploaded logs are metadata-only unless parsed content exists.
- Live-session log uploads now update backend session-scoped Expert Desk metadata (not just frontend-local context), so later typed + voice turns see updated log receipt status.
- Frontstage recommendation launch still binds session-linked Expert Desk context in frontend `sessionStorage` for current demo visuals (context strip/assist rail), but this is now complementary to backend session metadata handoff.
- Frontstage flow now includes `/demo/summary/:sessionId` as an explicit post-session handoff step, assembled from real session/transcript API data plus any session-linked Expert Desk context found in frontend `sessionStorage`.
- Frontstage flow now includes a shared lightweight progress indicator across `/demo` → `/demo/intake` → `/demo/recommendation` → `/visual-session/:sessionId` → `/demo/summary/:sessionId` for coherent walkthrough narration.
- Live visual-session wrap-up copy is now explicit and honest: navigation to summary/handoff does not imply backend session termination.
- Expert Desk intake readiness now reflects current field validity plus save state; stale “ready” status is cleared if required fields become invalid again.
- Summary now surfaces the latest saved local handoff request type/timestamp/note and explicitly labels it as frontend-local-only (not sent to backend/CRM/calendar/queue/ticketing systems).
- Expert Desk intake now uses a typed environment dropdown (`VMware`, `AWS`) and shows VMware-first recommended log guidance with frontend-local upload capture.
- VMware intake guidance is explicitly environment-driven and states that log upload is optional: the AI VMware expert can still assist without logs and may request them during live session as needed.
- Live visual session now includes frontend-local log upload capture tied to session-linked Expert Desk context so operators can see whether logs were provided.
- Visual stage assistant naming is configurable in the frontend using `VITE_ASKCHIP_ASSISTANT_DISPLAY_NAME` (default: `Chip`).
