# AskChappy Local-First Release Checklist

Use this checklist before local production/local MVP handoff.

## Verification commands
- [ ] `npm install`
- [ ] `npm test`
- [ ] `npm run lint`
- [ ] `npm run verify`
- [ ] `npm run dev` launches the AskChappy React/router scaffold at `http://127.0.0.1:4173/chappy`
- [ ] `npm run start` aliases the same local-first runtime workflow
- [ ] `npm run build:local-runtime` succeeds (production-style local build)
- [ ] `npm run smoke:local-runtime` passes as noninteractive app-shell wiring check (`dist/index.html` + built asset entry)

- [ ] Confirm browser-local persistence restores sessions/transcripts/metadata.askchappy/events across reloads.
- [ ] Confirm malformed local persistence payload recovers safely without app crash.
## Contract and route checks
- [ ] Confirm canonical routes are active and unchanged (`/chappy`, `/chappy/session/:sessionId`, `/chappy/summary/:sessionId`, admin routes).
- [ ] Confirm retired `/demo*` and `/visual-session*` routes remain inactive.
- [ ] Confirm transcript model uses `text` and does not use `content`.
- [ ] Confirm mode changes are kept in metadata/session events (not fake transcript messages).
- [ ] Confirm summary/recap remains grounded in canonical transcript + metadata.

## Auth/admin checks
- [ ] Confirm email-only local auth behavior is unchanged.
- [ ] Confirm `jsiejk@ddn.com` resolves to `admin` and other emails to `standard_user`.
- [ ] Confirm Voice Studio controls are admin-only and not shown in normal user sessions.
- [ ] Confirm avatar admin controls are admin-only and not shown in normal user sessions.

## Local-first production and asset safety checks
- [ ] Confirm local-first/local production/local MVP terminology is used.
- [ ] Confirm no private voice/avatar assets were committed.
- [ ] Confirm no model/cloud/db integrations were accidentally introduced.
- [ ] Confirm Phase 17 assistant runtime target remains local Ollama (`OLLAMA_BASE_URL` default `http://127.0.0.1:11434`; `OLLAMA_MODEL` default `gemma3:4b`; optional `OLLAMA_KEEP_ALIVE=30m`; optional `OLLAMA_NUM_CTX=8192`).
- [ ] Confirm no OpenAI runtime, no hosted/cloud LLM SDK, and no cloud LLM API key configuration was introduced.
- [ ] Confirm missing Ollama runtime or missing local model yields a clear local runtime not-configured state (no fake assistant intelligence).
- [ ] Confirm no DDN document ingestion/upload workflow was introduced.
- [ ] Confirm no file upload workflow for content grounding was introduced.
- [ ] Confirm no content grounding, embeddings, vector database/search, or RAG runtime was introduced.
- [ ] Confirm no proprietary DDN content bundle or knowledge-base management workflow was introduced.
- [ ] Confirm standard voice remains active/default for synthesis.
- [ ] Confirm cloned voice readiness gate does not claim active cloned synthesis without approved provider adapter + prerequisites.
- [ ] Confirm standard local TTS direction is Kokoro/kokoro-onnx and remains default/available.
- [ ] Confirm future STT direction is faster-whisper (no cloud STT provider introduced).
- [ ] Confirm no cloud voice provider runtime was added.

- [ ] Confirm Standard voice remains explicitly selected when cloned config is missing/incomplete, consent is false, publication is not `published`, `enabled` is false, adapter is missing, or readiness has errors.
