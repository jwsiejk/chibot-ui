# AskChappy Current Implementation Status (After Phase 21C)

## Completed phases
- Phase 1: app skeleton and canonical route scaffold
- Phase 2: shared contracts and validation helpers
- Phase 3: email-only local auth + admin role mapping
- Phase 4: local in-memory sessions + canonical transcript engine
- Phase 5: Chappy entry + Zoom-like session scaffold
- Phase 6: guided mode overlays + mode-change event handling
- Phase 7: deterministic transcript-grounded summary/recap
- Phase 8: admin dashboard + Voice Studio shell + avatar admin shell
- Phase 9: TTS provider interface + local fallback voice runtime
- Phase 10: cloned voice integration safely blocked/documented
- Phase 11: state-aware Chappy avatar placeholder scaffold
- Phase 12: local-first production hardening and packaging docs/scripts
- Phase 13: cloned voice provider contract package and readiness gate
- Phase 14: local-first start/dev runtime workflow
- Phase 15: standard-vs-cloned voice mode selection guardrails
- Phase 16: browser-local persistence adapter for sessions/transcripts/metadata/events (local runtime only; no backend/database/cloud persistence)
- Phase 17: local Ollama typed assistant runtime
- Phase 18: local Kokoro/kokoro-onnx standard TTS output
- Phase 19: local faster-whisper STT / browser microphone input
- Phase 20A: local runtime readiness/hardening
- Phase 20B: admin local GPU validation panel and operator guidance updates
- Phase 21C: local runtime venv/env cleanup for `.venv-local-runtime` documentation/template/ignore guardrails

## What is implemented
- Admin-only local GPU validation panel on `/admin` with typed status reporting for Ollama, faster-whisper STT, and Kokoro ONNX provider visibility (no fake GPU claims).
- Canonical route surface for `/chappy`, `/chappy/session/:sessionId`, `/chappy/summary/:sessionId`, admin pages, and `/dev` diagnostics path.
- Centralized shared contracts under `shared/contracts` for transcript, session, modes, auth, and voice lifecycle.
- Canonical transcript model (`text`, `role`, `source`) shared across typed/voice pathways.
- Session event model that keeps internal events separate from user-visible transcript records.
- Local-first deterministic recap generation from transcript + metadata.
- Admin-only Voice Studio and avatar control surfaces.
- Avatar state placeholder aligned to session lifecycle states.
- Local Kokoro/kokoro-onnx standard TTS runtime (local HTTP only) consistent with transcript-first voice constraints.
- Standard voice remains explicitly active/default.
- Optional cloned voice is guarded and requires both readiness checks and explicit real provider adapter availability.
- AskChappy runs normally without cloned voice assets.
- Browser-local persistence survives reloads for session records, canonical transcript messages, `metadata.askchappy`, and session events.
- Local Ollama typed assistant runtime for AskChappy typed chat via local HTTP API only.
- Ollama defaults: `OLLAMA_BASE_URL=http://127.0.0.1:11434` and `OLLAMA_MODEL=gemma3:4b`.
- Optional Ollama config: `OLLAMA_KEEP_ALIVE`, `OLLAMA_NUM_CTX`.
- No OpenAI/cloud runtime integration.
- No RAG/content grounding/document ingestion in the assistant runtime.
- Local faster-whisper STT with browser microphone capture feeding canonical user transcript `text` with `source: voice`.

## Intentionally not implemented
- OpenAI runtime integration
- Hosted/cloud LLM SDK integration
- DDN-specific uploaded document ingestion
- Content grounding, embeddings, vector search, or RAG pipeline
- Proprietary DDN content bundle / knowledge-base management workflows
- Real cloned voice provider adapter/runtime
- Real avatar assets, visemes, 3D rendering, or speaking animation
- Database-backed persistence
- Cloud deployment/runtime targets
- Enterprise auth hardening, OAuth, SSO, password login

## Current verification status
- `npm test`: passing
- `npm run lint`: passing
- `npm run verify`: passing
- `npm run build:local-runtime`: passing
- `npm run smoke:local-runtime`: passing

## Phase 21C local runtime venv/env notes
- Added `.venv-local-runtime/` to gitignore so dedicated local Python runtime environments are never committed.
- `.env.example` now includes safe local placeholders:
  - `LOCAL_RUNTIME_PYTHON=.\.venv-local-runtime\Scripts\python.exe`
  - `LOCAL_RUNTIME_VENV=.venv-local-runtime`
- Runtime runner commands remain machine-local `.env.local` values:
  - `KOKORO_TTS_RUN_COMMAND`
  - `FASTER_WHISPER_RUN_COMMAND`
- GPU dependency setup guidance is documented using `onnxruntime-gpu`, `kokoro-onnx`, and `faster-whisper`, with validation for `CUDAExecutionProvider` + `CPUExecutionProvider`.
- Installing packages alone does not satisfy runtime requirements; AskChappy still depends on local HTTP services at `http://127.0.0.1:8880` (Kokoro TTS) and `http://127.0.0.1:8890` (faster-whisper STT).

## Active route map
- `/`
- `/chappy`
- `/chappy/session/:sessionId`
- `/chappy/summary/:sessionId`
- `/dev`
- `/admin`
- `/admin/voice`
- `/admin/avatar`

Retired and inactive:
- `/demo*`
- `/visual-session*`

## Core contracts in use
- Transcript uses `text` (never `content`) and canonical role/source semantics.
- Session mode defaults to `open_qa` and guided modes behave as overlays.
- Session and metadata events capture internal transitions (including mode changes) outside visible transcript.
- Summary/recap remains grounded in canonical transcript + metadata.

## Voice/avatar status
- Standard voice path remains active/default.
- Cloned voice readiness gate exists.
- Cloned voice is not selected for synthesis unless a future real provider adapter is explicitly available.
- No real cloned voice provider adapter/runtime is implemented.
- Avatar runtime: placeholder, state-aware UI only; no real likeness/media assets.

## Deferred scope and next recommended work
- Content grounding / DDN document ingestion / RAG is deferred for now. Future content grounding work remains out of scope until explicitly re-prioritized.
- Keep local browser persistence schema-versioned and add migrations only when needed.
- Cloned voice adapter implementation remains explicitly deferred until provider/config/audio/consent prerequisites are available and approved.


## Phase 20A local runtime hardening
- Added local runtime readiness checks for Ollama runtime/model, Kokoro TTS, faster-whisper STT, browser microphone availability, standard voice default, and cloned voice optional/gated status.
- Readiness checks use local HTTP only and never append transcript messages.
- Session state transitions are hardened to recover to ready after STT/Ollama/TTS failures without fake transcript events.
- Content grounding / DDN document ingestion / RAG is deferred for now. Future content grounding work remains out of scope until explicitly re-prioritized.

- Kokoro readiness now prefers non-synthesis health probes (`/health`, then `/v1/health`) and uses fixed-text synthetic `/v1/tts` fallback only when health endpoints are unsupported; readiness never exposes synthetic audio/text artifacts.


## Phase 20B local runtime operator guide and validation pass
- Added operator-focused runbook: `docs/LOCAL_RUNTIME_OPERATOR_GUIDE.md`.
- Standardized required local runtime defaults for Ollama, Kokoro/kokoro-onnx, and faster-whisper in one operator reference.
- Documented required service start order and full local runtime validation pass.
- Added troubleshooting matrix for runtime reachability/readiness, microphone, STT/TTS behavior, and local persistence recovery.
- Reaffirmed no scope drift: no RAG/content grounding/DDN ingestion, no cloud providers, no cloned voice provider adapter runtime, no real avatar/visemes, no database/cloud persistence.

## Phase 21 session UX polish and local runtime clarity
- `/chappy/session/:sessionId` now shows a focused session status area with friendly labels for `ready`, `listening`, `transcribing`, `thinking`, `speaking`, and recoverable `error` states.
- Empty transcript now invites users to ask Chappy anything via typed or voice input.
- Runtime readiness remains visible with status + reason text in a minimal details panel (non-diagnostic-heavy).
- Voice flow clarifies recording/transcribing/no-speech and preserves canonical transcript behavior (no fake messages on STT failures/no-speech).
- TTS unavailable behavior is explicit and non-blocking: assistant text remains visible and no fake transcript messages are created.

## Phase 21B (Windows local runtime startup scripts)
- Added committed PowerShell scripts under `scripts/` for local runtime startup/check workflows:
  - `check-local-runtime.ps1`
  - `start-kokoro-tts.ps1`
  - `start-faster-whisper-stt.ps1`
  - `start-local-runtime.ps1`
- Scripts are local-first only. `start-local-runtime.ps1` requires `.env.local` and acts as a preflight orchestrator (not a process manager). Focused Kokoro/faster-whisper scripts require explicit local runner commands instead of guessing or auto-installing.
- Scripts do not create transcript messages and do not use cloud/OpenAI/hosted providers.

## Phase 21D (current)
- Added committed local HTTP runtime wrappers for local GPU-capable TTS/STT under `services/local-runtime/`.
- Added `services/local-runtime/requirements.txt` for local Python runtime dependencies only (no cloud/OpenAI SDKs).
- `.env.example` now includes wrapper-backed default runner commands for:
  - `KOKORO_TTS_RUN_COMMAND`
  - `FASTER_WHISPER_RUN_COMMAND`
- Wrappers are local-host defaults (`127.0.0.1`) and expose expected AskChappy endpoints:
  - Kokoro: `/health`, `/v1/health`, `/v1/tts`
  - faster-whisper: `/health`, `/v1/transcribe`
- CUDA/provider/device readiness is surfaced honestly from wrapper health payloads; no fake GPU claims are introduced.

## Phase 22 correction pass: force true Zoom-room layout
- AskChappy now uses a modern global CSS shell instead of raw browser-default scaffold styling.
- `/chappy` login gate is a centered branded local-first modal/card with email-only auth behavior unchanged.
- `/chappy` entry is a polished hero/card with primary **Join Chappy Room** CTA and guided mode cards; stale Phase 5 scaffold wording removed.
- `/chappy/session/:sessionId` now renders a Zoom-like room layout with prominent stage, session status bar, transcript panel, input controls area, compact runtime readiness disclosure, and right rail modes.
- Transcript rows and typed/voice controls are modernized visually without changing canonical transcript/runtime contracts.
- Admin dashboard receives light card-based shell styling; no new admin capabilities introduced.

- `/chappy` is now a pre-call lobby with centered Chappy room preview and compact guided mode chips.
- `/chappy/session/:sessionId` is now a true meeting-room shell (`meeting-room`) with sticky top bar, dominant Chappy stage (`chappy-video-tile`), right transcript panel (`meeting-chat-panel`), and sticky bottom toolbar (`meeting-toolbar`).
- Recorder-style wording and stage debug metadata are removed from normal user session UI.


## Phase 22 layout containment cleanup
- Session room now applies viewport lock behavior (`body.session-viewport-lock`) so `/chappy/session/:sessionId` prevents page-level scrolling while active.
- Meeting shell is explicitly viewport-contained (`height: 100vh`, `overflow: hidden`) with stable top bar, fill-center body, and persistent bottom toolbar.
- Meeting body and nested panes use `min-height: 0` to preserve nested scrolling correctness in grid/flex layouts.
- Transcript panel remains visible in-room and transcript message list is the dedicated vertical scroller (`overflow-y: auto`) as conversation grows.
- Guided/current mode panel is compact and internally scrollable so mode content no longer forces the entire page taller than the viewport.
- `/chappy` lobby remains viewport-friendly; if content exceeds available space, internal container scrolling is used instead of overflowing the browser page.


## Phase 22D update
- Phase 22D local wrapper CORS cleanup: Kokoro/faster-whisper wrappers now use FastAPI CORSMiddleware with explicit local Vite origins (`http://127.0.0.1:4173`, `http://localhost:4173`) by default, optional `--allowed-origin` overrides, localhost bind defaults unchanged, and existing `/health`/`/v1/health`/`/v1/tts`/`/v1/transcribe` contracts preserved.

- /chappy now presents a true pre-call meeting lobby with centered room preview, compact guided mode chips, and Join Chappy Room CTA.
- /chappy/session/:sessionId now uses a true Zoom-style meeting-room shell with top bar, dominant Chappy stage tile, right transcript panel, and persistent bottom toolbar.
- Toolbar semantics updated: Mic is push-to-talk user input, Modes is compact overlay access, Runtime is compact disclosure, and admin-only Admin opens runtime modal.
- Assistant responses now auto-play via TTS by default when available/unmuted; transcript remains canonical regardless of TTS success.
- Speak control replaced by Mute Chappy / Unmute Chappy output toggle; muted mode is transcript-only and non-blocking.
- Added Admin Runtime Console modal with readiness, GPU validation, local endpoints, troubleshooting hints, and bounded client diagnostics (max 25 events, in-memory only).
- Session shell is viewport-contained with transcript message list as the primary scrolling area.

## STT diagnostics cleanup update
- faster-whisper wrapper now emits traceback-oriented server-side error logs for `/v1/transcribe` failures and returns safe structured error details for operator troubleshooting.
- STT adapter now differentiates network reachability failures (`runtime_unreachable`) from runtime processing failures (`transcription_failed`) and malformed client/runtime interactions (`invalid_response`).
- Session UI messaging now reports transcription-processing failures clearly instead of incorrectly labeling all failures as runtime unreachable.

## TTS synthesis/playback diagnostics cleanup update
- Kokoro TTS provider now maps unavailable states with finer reasons: `not_configured`, `runtime_unreachable`, `request_cancelled`, `request_rejected`, `synthesis_failed`, and `invalid_response`.
- Provider output now supports optional safe fields (`unavailable_message`, `provider_error_detail`) to surface runtime detail without changing transcript contracts.
- Kokoro `/v1/tts` wrapper now logs synthesis exceptions with traceback and returns structured failure detail (`error`, `detail`, `voice`, `format`, `text_length`) without logging full assistant text.
- Session voice UX now separates TTS synthesis failures from browser playback failures, with distinct user guidance and bounded diagnostics events.
- Browser playback failures are non-blocking and no longer mislabeled as TTS unavailable.


## Phase 22E latency console accuracy cleanup
- Admin Runtime Console turn latency is local-only and in-memory (bounded to last 5 turns), with no persistence, no transcript text storage, and no telemetry export.
- “Time to Chappy speaking” now explicitly means time to audio playback start (`audio.play()` resolve), not end of spoken audio.
- Muted turns still record turn latency with assistant text-ready timing and explicit `TTS skipped: muted` and `Playback skipped: muted`.
- Failure stages are explicitly surfaced for STT, assistant generation, TTS synthesis, and playback start failures.
