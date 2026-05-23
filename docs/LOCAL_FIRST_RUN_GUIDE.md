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
There is no dedicated `start`/`dev` script in the current Phase 13 repository state.
Use route, contract, and runtime verification through the automated tests and lint checks while the local-first product scaffold is hardened.
Phase 14 is expected to define and add this workflow.

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
