# AskChappy Local-First Production Run Guide

This guide covers the current **local-first production** workflow for AskChappy.

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

`npm run build:local-runtime` performs a noninteractive production-style build via Vite.

`npm run smoke:local-runtime` performs the build and verifies the built app shell wiring (`dist/index.html` + built asset references) without requiring a long-running dev server.

## Current auth behavior
- Email-only local auth is used on `/chappy`.
- `jsiejk@ddn.com` resolves to `admin`.
- All other emails resolve to `standard_user`.

## Current known limitations
- No OpenAI/model runtime yet.
- Planned Phase 17 local runtime direction: Ollama (`OLLAMA_BASE_URL` default `http://127.0.0.1:11434`, `OLLAMA_MODEL` default `gemma3:4b`, optional `OLLAMA_KEEP_ALIVE=30m`, optional `OLLAMA_NUM_CTX=8192`).
- Missing Ollama/model in Phase 17 must show clear local runtime not-configured state (no fake assistant output, no cloud fallback).
- No RAG/DDN ingestion yet.
- No real cloned voice provider adapter yet; Phase 13 only added contract/readiness gating and standard voice remains active/default (planned standard local TTS direction: Kokoro/kokoro-onnx).
- No real avatar assets/visemes/3D rendering yet.
- No database persistence yet.
- Session data now persists in browser localStorage (browser-local only; no sync across devices/browsers).

## Local-first terminology and route policy
- AskChappy is local-first production software in a local production/local MVP deployment model.
- Retired `/demo*` and `/visual-session*` routes remain inactive historical routes.

- Standard voice remains active/default even when cloned voice config/provider adapter are missing or not ready; AskChappy runs normally without cloned voice assets.


## Local persistence behavior
- AskChappy stores a schema-versioned local payload in browser localStorage for session records, canonical transcript messages (`text` field), `metadata.askchappy`, and session events via the browser-local adapter (`services/askchappy-api/src/sessions/browserLocalSessionPersistenceAdapter.ts`).
- Mode changes persist as session events and do not create fake transcript messages.
- Malformed persisted payloads are discarded safely to restore clean local state.

## Phase 17 local assistant runtime
Set optional env vars for local Ollama runtime:
- `OLLAMA_BASE_URL` (default `http://127.0.0.1:11434`)
- `OLLAMA_MODEL` (default `gemma3:4b`)
- `OLLAMA_KEEP_ALIVE` (optional, e.g. `30m`)
- `OLLAMA_NUM_CTX` (optional, e.g. `8192`)
If Ollama is not reachable, AskChappy shows: "Local Ollama runtime is not configured or not reachable." No cloud fallback is used.


## Standard local Kokoro TTS configuration
- `KOKORO_TTS_BASE_URL`: local Kokoro/kokoro-onnx URL (default runtime target `http://127.0.0.1:8880`).
- `KOKORO_TTS_VOICE`: local voice id (default `af_sarah`).
- `KOKORO_TTS_FORMAT`: output format (default `wav`).
- `KOKORO_TTS_TIMEOUT_MS`: request timeout in milliseconds.
- If `KOKORO_TTS_BASE_URL` is not set, standard local voice remains selected/default but synthesis reports runtime not configured.
- TTS always consumes committed assistant transcript `text` and returns matching `spoken_text`.
- No STT/microphone input, cloud TTS SDK, or cloned voice provider adapter is added in Phase 18.


## Local faster-whisper STT configuration
- `FASTER_WHISPER_BASE_URL`: local faster-whisper HTTP runtime URL (default target `http://127.0.0.1:8890`).
- `FASTER_WHISPER_MODEL`: model id (default `base.en`).
- `FASTER_WHISPER_LANGUAGE`: language hint (default `en`).
- `FASTER_WHISPER_TIMEOUT_MS`: request timeout in milliseconds (default `20000`).
- Missing runtime/config behavior: if STT runtime is missing or unreachable, AskChappy returns clear local errors (`not_configured` or `runtime_unreachable`) and does not append fake transcript messages.
- Voice input is committed as canonical user transcript `text` with `source: voice`; no separate voice transcript model exists.
- No cloud STT/speech SDK or fallback is used.


## Phase 20A local runtime hardening
- Added local runtime readiness checks for Ollama runtime/model, Kokoro TTS, faster-whisper STT, browser microphone availability, standard voice default, and cloned voice optional/gated status.
- Readiness checks use local HTTP only and never append transcript messages.
- Session state transitions are hardened to recover to ready after STT/Ollama/TTS failures without fake transcript events.
- Content grounding/RAG remains deferred (Phase 20+).
