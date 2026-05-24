# AskChappy

AskChappy is local-first production software for DDN partner enablement workflows.

## Core docs
- V1 specification: `docs/ASKCHAPPY_V1_SPEC.md`
- Implementation contracts: `docs/IMPLEMENTATION_CONTRACTS.md`
- Build playbook: `docs/BUILD_PLAYBOOK.md`
- Local-first run guide: `docs/LOCAL_FIRST_RUN_GUIDE.md`
- Local runtime operator guide: `docs/LOCAL_RUNTIME_OPERATOR_GUIDE.md`
- Current implementation status: `docs/CURRENT_IMPLEMENTATION_STATUS.md`
- Local-first release checklist: `docs/LOCAL_FIRST_RELEASE_CHECKLIST.md`
- Dependency review: `docs/DEPENDENCY_REVIEW.md`
- Phase 10 cloned voice blocker note: `docs/PHASE10_CLONED_VOICE_BLOCKER_NOTE.md`

## Local verification
```bash
npm test
npm run lint
npm run verify
```

## Local run
```bash
npm run start
```

## Local environment configuration
1. Copy `.env.example` to `.env.local`.
2. Edit `.env.local` for your machine-local runtime endpoints/models as needed.
3. Run `npm run start`.

Windows PowerShell startup:
```powershell
copy .env.example .env.local
# edit .env.local
npm run start
```


Default local runtime URL: `http://127.0.0.1:4173/chappy`.

Additional local runtime commands:
```bash
npm run build:local-runtime
npm run smoke:local-runtime
```

## Terminology and route policy
- Deployment model: local-first, local production/local MVP.
- Retired `/demo*` and `/visual-session*` routes remain inactive historical routes.


## Phase 19 update
- Added local faster-whisper STT with browser microphone input.
- Voice input is appended as canonical transcript messages using `text` with `source: voice`.
- No separate voice transcript model was added.
- No cloud STT/speech SDK was added; no cloud fallback exists.
- No RAG/document ingestion/content grounding added; content grounding / DDN document ingestion / RAG is deferred for now.
- No cloned voice provider adapter was added.


## Phase 20A local runtime hardening
- Added local runtime readiness checks for Ollama runtime/model, Kokoro TTS, faster-whisper STT, browser microphone availability, standard voice default, and cloned voice optional/gated status.
- Readiness checks use local HTTP only and never append transcript messages.
- Session state transitions are hardened to recover to ready after STT/Ollama/TTS failures without fake transcript events.
- Content grounding / DDN document ingestion / RAG is deferred for now. Future content grounding work remains out of scope until explicitly re-prioritized.
