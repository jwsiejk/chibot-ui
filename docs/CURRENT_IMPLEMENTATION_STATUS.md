# AskChappy Current Implementation Status (After Phase 13)

## Completed phases
- Phase 14: local-first start/dev runtime workflow
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

## What is implemented
- Canonical route surface for `/chappy`, `/chappy/session/:sessionId`, `/chappy/summary/:sessionId`, admin pages, and `/dev` diagnostics path.
- Centralized shared contracts under `shared/contracts` for transcript, session, modes, auth, and voice lifecycle.
- Canonical transcript model (`text`, `role`, `source`) shared across typed/voice pathways.
- Session event model that keeps internal events separate from user-visible transcript records.
- Local-first deterministic recap generation from transcript + metadata.
- Admin-only Voice Studio and avatar control surfaces.
- Avatar state placeholder aligned to session lifecycle states.
- Local fallback TTS runtime consistent with transcript-first voice constraints.
- Verification scripts for test/lint (`npm run verify`).

## Intentionally not implemented
- OpenAI/model runtime integration
- Real-time or batch model inference runtime
- RAG or DDN ingestion pipeline
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
- Voice runtime: standard voice path remains active/default via provider abstraction.
- Cloned voice runtime: readiness gate/contract added in Phase 13, but no real provider adapter/runtime is implemented.
- Avatar runtime: placeholder, state-aware UI only; no real likeness/media assets.

## Phase 10 blocker summary
Cloned voice runtime integration remains blocked until provider selection, local config shape, published profile configuration, and admin publication gating details are approved for local production use.

## Next recommended work after Phase 14
- Phase 15: cloned voice provider adapter is only next when provider/config/audio/consent prerequisites are actually available and approved.
- Phase 16: plan enterprise-auth replacement path while preserving local-first development/testing workflow.
