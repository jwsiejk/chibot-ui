# AskChappy Current Implementation Status (After Phase 16)

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

## What is implemented
- Canonical route surface for `/chappy`, `/chappy/session/:sessionId`, `/chappy/summary/:sessionId`, admin pages, and `/dev` diagnostics path.
- Centralized shared contracts under `shared/contracts` for transcript, session, modes, auth, and voice lifecycle.
- Canonical transcript model (`text`, `role`, `source`) shared across typed/voice pathways.
- Session event model that keeps internal events separate from user-visible transcript records.
- Local-first deterministic recap generation from transcript + metadata.
- Admin-only Voice Studio and avatar control surfaces.
- Avatar state placeholder aligned to session lifecycle states.
- Local fallback TTS runtime consistent with transcript-first voice constraints.
- Standard voice remains explicitly active/default.
- Optional cloned voice is guarded and requires both readiness checks and explicit real provider adapter availability.
- AskChappy runs normally without cloned voice assets.
- Verification scripts for test/lint (`npm run verify`).
- Browser-local persistence survives reloads for session records, canonical transcript messages, `metadata.askchappy`, and session events.

## Intentionally not implemented
- OpenAI runtime integration
- Hosted/cloud LLM SDK integration
- Real-time or batch model inference runtime
- DDN-specific uploaded document ingestion
- Content grounding, embeddings, vector search, or RAG pipeline
- Proprietary DDN content bundle / knowledge-base management workflows
- Real cloned voice provider adapter/runtime
- STT/microphone/browser recording features
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

## Phase 10 blocker summary
Cloned voice runtime integration remains blocked until provider selection, local config shape, published profile configuration, and admin publication gating details are approved for local production use.

## Next recommended work after Phase 16
- Phase 17: assistant/model runtime scaffold using local open-source LLM knowledge via a local Ollama runtime (default model: `gemma3:4b`) (local-first production path; no DDN document ingestion dependency).
- Phase 17 planned config: `OLLAMA_BASE_URL` default `http://127.0.0.1:11434`, `OLLAMA_MODEL` default `gemma3:4b`, optional `OLLAMA_KEEP_ALIVE=30m`, optional `OLLAMA_NUM_CTX=8192`.
- Phase 17 excludes OpenAI runtime, hosted/cloud LLM SDKs, and cloud LLM API keys.
- If Ollama/local model is unavailable, show a clear local runtime not-configured state (no fake assistant intelligence).
- Phase 18: local Kokoro/kokoro-onnx TTS output (standard local voice default path).
- Phase 19: local faster-whisper STT / browser microphone input, unless project direction changes.
- Phase 20 or later: content grounding / document ingestion / RAG remains required follow-on work (DDN document upload, proprietary bundles, embeddings, vector search, and knowledge-base workflows).
- Standard local voice target remains Kokoro/kokoro-onnx and cloned voice remains optional/gated.
- Keep local browser persistence schema-versioned and add migrations only when needed.
- Do not block the first usable assistant conversation/runtime phase on content grounding.
- Cloned voice adapter implementation remains explicitly deferred until provider/config/audio/consent prerequisites are available and approved.


- Implementation boundary: `services/askchappy-api/src/sessions/browserLocalSessionPersistenceAdapter.ts` is the explicit browser-local persistence adapter used by the local-first runtime scaffold.

## Phase 17 update
- Local Ollama typed assistant runtime is implemented for AskChappy typed chat.
- Default local model is `gemma3:4b` and default base URL is `http://127.0.0.1:11434`.
- No OpenAI/cloud runtime was added.
- No RAG/content grounding/document ingestion was added.
- Phase 18 remains local Kokoro/kokoro-onnx TTS.
- Phase 19 remains local faster-whisper STT.
- Content grounding/RAG remains Phase 20+.
