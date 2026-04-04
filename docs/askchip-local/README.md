# AskChip Local Run Guide

## Contract artifact
- The authoritative, reviewable AskChip Local v1 contract now lives in the repo root at `AskChip Local v1 Contract.md`.
- The legacy `AskChip Local v1 Contract.docx` remains as an export artifact, but pull-request contract updates should be made in the markdown file.
- Expert Desk frontstage progress is tracked in `docs/askchip-local/expert-desk-demo.md`.

## Localhost defaults
- Frontend dev server: `http://127.0.0.1:5173`
- Backend API: `http://127.0.0.1:8000`
- Backend WebSocket host: `ws://127.0.0.1:8000`
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
- AskChip Local defaults to `OLLAMA_MODEL=gemma3:4b` for local generation.
- Pull the default model locally before starting the API:
  ```bash
  ollama pull gemma3:4b
  ```
- AskChip Local also sets explicit Ollama runtime request defaults for local responsiveness:
  - `OLLAMA_KEEP_ALIVE=30m`
  - `OLLAMA_NUM_CTX=8192`
  - `OLLAMA_NUM_PARALLEL=1`
- `OLLAMA_NUM_PARALLEL=1` is intentional for this local voice-first assistant profile, keeping memory pressure predictable for streaming chat + STT/TTS on one machine.
- Ollama memory use scales with effective parallelism × context length. Increasing `OLLAMA_NUM_PARALLEL` and/or `OLLAMA_NUM_CTX` raises peak memory requirements.
- `OLLAMA_NUM_PARALLEL` is an Ollama server/runtime setting and is not a per-request `/api/chat` payload field in AskChip.
- Verify where Gemma is loaded (CPU/GPU split) with:
  ```bash
  ollama ps
  ```
- You can still override the model without code changes by setting `OLLAMA_MODEL` in your shell/environment before starting the API.
- `/api/v1/config` reports the active backend model and resolved Ollama runtime settings (`ollama_model`, `ollama_keep_alive`, `ollama_num_ctx`, `ollama_num_parallel`) plus requested/selected STT device and compute type.
- `/api/v1/readiness` always performs a local installed-model check for the configured `OLLAMA_MODEL`, even when `ASKCHIP_OLLAMA_WARMUP_ENABLED=false`; warm-up requests remain disabled in that mode.

## Backend virtual environment (required)
Use a dedicated backend environment at `services/askchip-api/.venv` so runtime diagnostics reflect the exact backend dependency set.

### Bash
1. Create and activate a Python 3.11+ virtual environment at `services/askchip-api/.venv`.
2. Install API dependencies:
   ```bash
   pip install -e .[dev]
   ```

### Windows + NVIDIA (supported GPU TTS setup)
Run the setup script from the repo root:

```powershell
./scripts/setup-askchip-local-windows-nvidia.ps1
```

This script:
- creates/uses `services/askchip-api/.venv`
- installs backend deps with `pip install -e .[dev]`
- removes CPU `onnxruntime`
- installs `onnxruntime-gpu`
- verifies `CUDAExecutionProvider` availability

> Use this script as the repo-supported GPU TTS installation path. It is intentionally the single documented path for Windows/NVIDIA GPU ONNX Runtime setup.

Activate the environment before running the backend:

```powershell
.\services\askchip-api\.venv\Scripts\Activate.ps1
```

## Start local services
Install UI dependencies once in `apps/askchip-ui`:

```bash
npm install
```

From the repo root:

- Windows:
  ```powershell
  ./scripts/run-askchip-local.ps1
  ```
- Bash:
  ```bash
  ./scripts/run-askchip-local.sh
  ```

Both run scripts require `services/askchip-api/.venv` and use that interpreter directly.

## TTS runtime diagnostics (GPU/CPU honesty)
- `ASKCHIP_TTS_DEVICE` defaults to `auto`.
- `auto` selects CUDA only when ONNX Runtime reports `CUDAExecutionProvider`; otherwise it falls back to CPU.
- Runtime fallback details are reported through `/api/v1/config` and `/api/v1/readiness`.

### Verify ONNX Runtime providers
From the backend `.venv`:

```bash
python -c "import onnxruntime as ort; print(ort.get_available_providers())"
```

### Verify backend diagnostics

```bash
curl http://127.0.0.1:8000/api/v1/config
curl http://127.0.0.1:8000/api/v1/readiness
```

### GPU success criteria
- `/api/v1/config` shows `tts_device = cuda`
- `/api/v1/config` shows `tts_provider = CUDAExecutionProvider`

### Honest fallback criteria
- `/api/v1/config` shows `tts_device = cpu`
- `/api/v1/config` includes `tts_warning` and `tts_fallback_reason` explaining why CUDA was unavailable

## Current frontend scope
- Typed chat is implemented, including transcript loading, session selection, and streaming assistant text updates.
- WebRTC foundation work is implemented for mic readiness, peer negotiation, explicit disconnect cleanup, and transport diagnostics only.
- Backend WebRTC peer/session lifetime is intentionally not tied to signaling WebSocket lifetime; explicit disconnect and backend orphan cleanup release peer sessions.
- Push-to-talk voice input is implemented through direct microphone capture plus backend faster-whisper transcription after release.
- WebRTC remains foundation-only for diagnostics/signaling and is not required for voice-turn capture or upload.
- Phase 6 adds local Kokoro assistant speech from the same canonical assistant message, now starting as soon as a stable sentence-level chunk is available while generation is still streaming.
- Wake word, always-open microphones, tools, RAG, and auth remain out of scope.

## Expert Desk session metadata notes
- Expert Desk session metadata now includes an optional typed VMware triage state at `metadata.expert_desk.vmware_triage`.
- The VMware triage state is session-scoped and persists across typed and voice turns so runtime triage flow can be reused turn-to-turn.
- For VMware persona sessions, backend runtime may update this triage state through a hidden per-turn extraction step before assistant response generation; transcript message shape is unchanged.
- VMware triage metadata now also tracks deterministic log-sufficiency fields per issue family (`log_sufficiency_status`, `required_logs`, `received_logs`, `missing_logs`, `optional_logs`, `log_guidance_summary`) based on uploaded file metadata names only.
- For VMware persona sessions that already have a triage `issue_family`, session metadata PATCH updates now immediately refresh deterministic log-sufficiency fields from the latest `uploaded_log_names` during live sessions (without waiting for the next committed turn).
- The same PATCH-time refresh now also recomputes deterministic policy fields on persisted triage state (`policy_next_move`, policy-aligned `conversation_stage`, and `next_best_question`) with non-regressive behavior (it avoids resetting an already-established issue-family flow back to `confirm_issue_family`/`issue_definition` when triage context is already present) so live log uploads keep VMware triage metadata coherent immediately.
- VMware trajectory transition events are now persisted for real VMware triage state changes only (`issue_family`, `conversation_stage`, `policy_next_move`, `log_sufficiency_status`, `resolution_status`) so developers can inspect deterministic troubleshooting progression without changing transcript contracts.
- Those transition events use `vmware.trajectory.*_changed` types with compact payload fields (`previous_value`, `current_value`, `source_path`, optional `turn_id`, optional `trace_id`) and are emitted from both turn runtime updates and PATCH-time refresh paths.
- AskChip remains honest about current log handling: uploaded file metadata can drive sufficiency guidance, but parsed-log findings are not claimed unless parsed content exists.
- Expert Desk Phase 7 adds real session-scoped backend artifact upload endpoints (`POST/GET /api/v1/sessions/{session_id}/artifacts`) with local storage persistence.
- Only standalone plain-text `vmkernel.log`, `vobd.log`, and `vpxd.log` are parsed in this phase. Unsupported artifacts are stored and labeled honestly (`uploaded_unsupported`).
- Parsed evidence is typed and stored separately from transcript text under `metadata.expert_desk.vmware_artifacts`; transcript contract remains unchanged.
- VMware live runtime guidance now includes a deterministic conversation-policy decision (next move + focused next question) derived from triage state, conversation stage, confidence, log sufficiency, and latest user feedback, while preserving the same transcript payload contract.
- VMware triage persistence now also stores deterministic policy outputs (`policy_next_move`, policy-aligned `conversation_stage`, and policy-focused `next_best_question`) in session metadata for consistent multi-turn state.
- VMware triage now normalizes `resolution_status` into a fixed set (`unresolved`, `monitoring`, `resolved`, `blocked_waiting_on_logs`, `blocked_waiting_on_user_action`, `needs_human_handoff`).
- VMware Expert Desk metadata now includes an optional typed `metadata.expert_desk.vmware_handoff` packet for structured summary/handoff framing grounded in persisted triage state, transcript facts, and uploaded log-name metadata only (no parsed-log claims in this phase).
- Transcript and turn payload contracts remain unchanged (`text` remains canonical transcript content).
