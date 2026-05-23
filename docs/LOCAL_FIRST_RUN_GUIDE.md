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
- Dedicated `build`/`typecheck` scripts are not included in this phase. A full repo typecheck currently requires Node type definitions that are not installable in this environment, so verification is intentionally test+lint based.

## Start/use local app scaffold
Use the dedicated local-first runtime script:

```bash
npm run start
```

This launches the AskChappy React/router scaffold through local TypeScript build output + Node static server at:
- `http://127.0.0.1:4173/chappy` (entry/login)
- `http://127.0.0.1:4173/chappy/session/:sessionId` (active session route after start)
- `http://127.0.0.1:4173/chappy/summary/:sessionId` (recap route)

Related scripts:
```bash
npm run dev
npm run build:local-runtime
npm run smoke:local-runtime
```

`npm run smoke:local-runtime` is a noninteractive check that verifies local runtime wiring by executing the runtime TypeScript build.

## Current auth behavior
- Email-only local auth is used on `/chappy`.
- `jsiejk@ddn.com` resolves to `admin`.
- All other emails resolve to `standard_user`.

## Current known limitations
- No OpenAI/model runtime yet.
- No RAG/DDN ingestion yet.
- No real cloned voice provider adapter yet; Phase 13 only added contract/readiness gating and standard voice remains active/default.
- No STT/browser microphone runtime yet.
- No real avatar assets/visemes yet.
- No database persistence yet.

## Local-first terminology and route policy
- AskChappy is local-first production software in a local production/local MVP deployment model.
- Retired `/demo*` and `/visual-session*` routes remain inactive historical routes.
