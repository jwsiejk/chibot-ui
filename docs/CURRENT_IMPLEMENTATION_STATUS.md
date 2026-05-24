# AskChappy Current Implementation Status (After Phase 20B)

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
