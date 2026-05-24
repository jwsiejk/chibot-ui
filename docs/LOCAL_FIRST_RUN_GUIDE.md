# AskChappy Local-First Production Run Guide

This guide covers the current **local-first production** workflow for AskChappy.

For the complete operator runbook (service start order, endpoint baseline, runtime validation pass, and troubleshooting), use:
- `docs/LOCAL_RUNTIME_OPERATOR_GUIDE.md`

## Prerequisites
- Node.js 20+
- npm 10+
- Local Git checkout of this repository

## Install
```bash
npm install
```

## Verification commands
```bash
npm test
npm run lint
npm run verify
```

Notes:
- `npm run verify` runs test and lint in sequence.
- `npm run build:local-runtime` builds the AskChappy React/router app with Vite into `dist/`.
- `npm run smoke:local-runtime` runs a noninteractive build + app-shell wiring check against `dist/index.html`.

## Start/use local app scaffold
Use the dedicated local-first runtime script:

```bash
npm run start
```

This launches the AskChappy React/router scaffold with the Vite local server at:
- `http://127.0.0.1:4173/chappy` (entry/login)
- `http://127.0.0.1:4173/chappy/session/:sessionId` (active session route after start)
- `http://127.0.0.1:4173/chappy/summary/:sessionId` (recap route)

Related scripts:
```bash
npm run dev
npm run build:local-runtime
npm run smoke:local-runtime
```

`npm run dev` and `npm run start` both run Vite on `127.0.0.1:4173` for the same local-first runtime.

Vite handles browser-history fallback for canonical React routes (`/chappy`, `/chappy/session/:sessionId`, `/chappy/summary/:sessionId`, `/admin`, etc.) during local-first runtime use.

## Runtime services and environment defaults
Operator-required local runtime dependencies and default local endpoint values are defined in:
- `docs/LOCAL_RUNTIME_OPERATOR_GUIDE.md`

This includes:
- Ollama (`OLLAMA_BASE_URL`, `OLLAMA_MODEL`)
- Kokoro/kokoro-onnx (`KOKORO_TTS_BASE_URL`, `KOKORO_TTS_VOICE`, `KOKORO_TTS_FORMAT`)
- faster-whisper (`FASTER_WHISPER_BASE_URL`, `FASTER_WHISPER_MODEL`, `FASTER_WHISPER_LANGUAGE`)
- Required service start order and readiness validation flow

## Current auth behavior
- Email-only local auth is used on `/chappy`.
- `jsiejk@ddn.com` resolves to `admin`.
- All other emails resolve to `standard_user`.

## Current known limitations
- Content grounding / DDN document ingestion / RAG is deferred for now.
- No OpenAI/cloud/hosted providers are used.
- No real cloned voice provider adapter is implemented.
- No real avatar assets/visemes/3D rendering are implemented.
- No database persistence is implemented.
- Session data persists in browser localStorage only (browser-local; no sync across devices/browsers).

## Local-first terminology and route policy
- AskChappy is local-first production software in a local production/local MVP deployment model.
- Retired `/demo*` and `/visual-session*` routes remain inactive historical routes.
- Standard voice remains active/default even when cloned voice prerequisites are not met.

## Local persistence behavior
- AskChappy stores a schema-versioned local payload in browser localStorage for session records, canonical transcript messages (`text` field), `metadata.askchappy`, and session events via the browser-local adapter (`services/askchappy-api/src/sessions/browserLocalSessionPersistenceAdapter.ts`).
- Mode changes persist as session events and do not create fake transcript messages.
- Malformed persisted payloads are discarded safely to restore clean local state.

## Phase 20A+ runtime readiness policy reminder
- Runtime readiness checks are local-only and must never append transcript messages.
- Kokoro readiness prefers non-synthesis health probes (`/health`, then `/v1/health`) and only falls back to fixed-text synthetic `/v1/tts` when health paths are unsupported.
- For full readiness validation procedure and troubleshooting matrix, use `docs/LOCAL_RUNTIME_OPERATOR_GUIDE.md`.
