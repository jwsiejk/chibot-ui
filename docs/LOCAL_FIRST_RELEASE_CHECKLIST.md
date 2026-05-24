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


- [ ] Confirm admin-only `/admin` Local GPU Validation panel is visible for admin users.
- [ ] Confirm standard users cannot access Local GPU Validation panel.
- [ ] Confirm `/chappy/session/:sessionId` does not expose GPU validation controls.
- [ ] Confirm unknown GPU statuses include honest manual `nvidia-smi -l 1` guidance when service APIs lack device/provider fields.

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
- [x] Confirm local STT implementation uses faster-whisper (no cloud STT provider introduced).
- [ ] Confirm no cloud voice provider runtime was added.
- [ ] Confirm local voice input/output does not create separate transcript models (voice input -> canonical user transcript text; voice output <- canonical assistant transcript text).

- [ ] Confirm Standard voice remains explicitly selected when cloned config is missing/incomplete, consent is false, publication is not `published`, `enabled` is false, adapter is missing, or readiness has errors.

- [x] Phase 17 local Ollama typed assistant runtime wired (`gemma3:4b` default).
- [x] No OpenAI/cloud LLM runtime added.
- [x] No RAG/content grounding/document ingestion added.
- [x] Phase 18 local Kokoro/kokoro-onnx TTS (standard local voice default path).
- [x] Phase 19 local faster-whisper STT.

- [x] No cloud STT/speech SDK added.
- [x] No cloud TTS/voice SDK added.
- [x] No cloned voice provider adapter added.
- [x] No RAG/document ingestion/content grounding added.


## Phase 20A local runtime hardening
- Added local runtime readiness checks for Ollama runtime/model, Kokoro TTS, faster-whisper STT, browser microphone availability, standard voice default, and cloned voice optional/gated status.
- Readiness checks use local HTTP only and never append transcript messages.
- Session state transitions are hardened to recover to ready after STT/Ollama/TTS failures without fake transcript events.
- Content grounding / DDN document ingestion / RAG is deferred for now.

- [ ] Confirm Kokoro readiness prefers non-synthesis health probes (`/health` then `/v1/health`) and only falls back to fixed-text synthetic `/v1/tts` when health endpoints are unsupported (no synthetic output exposed).

## Phase 20B local runtime operator validation
- [ ] Confirm operator runbook exists and is current: `docs/LOCAL_RUNTIME_OPERATOR_GUIDE.md`.
- [ ] Confirm required local runtime defaults are documented (`OLLAMA_*`, `KOKORO_TTS_*`, `FASTER_WHISPER_*`).
- [ ] Confirm required start order is documented and followed (Ollama -> model pull/check -> Kokoro -> faster-whisper -> `npm run start`).
- [ ] Confirm operator validation pass is executable end-to-end (`/chappy`, start session, readiness panel, typed input, microphone input, canonical transcript checks, standard voice playback).
- [ ] Confirm troubleshooting coverage includes Ollama runtime/model failures, Kokoro health/fallback behavior, faster-whisper failures, microphone denial, TTS/STT runtime issues, and localStorage malformed recovery.
- [ ] Confirm runbook explicitly states out-of-scope items (no RAG/content grounding/DDN ingestion, no cloud fallback/providers, no cloned voice provider adapter, no real avatar/visemes, no database/cloud persistence).

## Phase 21 session UX checks
- [ ] Confirm `/chappy/session/:sessionId` visibly shows ready/listening/transcribing/thinking/speaking/error session states with user-friendly messaging.
- [ ] Confirm voice and typed empty-session prompt invites “Ask Chappy anything” behavior (not diagnostics-first framing).
- [ ] Confirm failed/no-speech STT, failed Ollama, and failed TTS recover without fake transcript messages.
- [ ] Confirm typed turns remain canonical user transcript entries (`source: typed`, `text`).
- [ ] Confirm voice turns remain canonical user transcript entries (`source: voice`, `text`) only on successful STT.
- [ ] Confirm TTS unavailable state is clearly non-blocking and assistant text remains visible.
- [ ] Confirm normal session keeps admin-only controls hidden (GPU validation, Voice Studio, avatar admin controls).

## Phase 21B runtime script checks
- [ ] Confirm `.\scripts\check-local-runtime.ps1` prints status table for Ollama runtime/model, Kokoro, faster-whisper, and AskChappy URL.
- [ ] Confirm `.\scripts\start-kokoro-tts.ps1` validates local asset path and requires `KOKORO_TTS_RUN_COMMAND`.
- [ ] Confirm `.\scripts\start-faster-whisper-stt.ps1` validates local config and requires `FASTER_WHISPER_RUN_COMMAND`.
- [ ] Confirm `.\scripts\start-local-runtime.ps1` acts as preflight orchestrator (not process manager), fails non-zero on missing services, and does not hide failures.
- [ ] Confirm `nvidia-smi -l 1` manual guidance is documented; no native Windows GPU process helper is claimed.

## Phase 21C local runtime venv/env checks
- [ ] Confirm `.venv-local-runtime/` is gitignored and treated as local-only (never committed).
- [ ] Confirm `.env.local` remains gitignored and `.env.example` remains committed template (not ignored).
- [ ] Confirm `.env.example` includes `LOCAL_RUNTIME_PYTHON=.\.venv-local-runtime\Scripts\python.exe` and `LOCAL_RUNTIME_VENV=.venv-local-runtime`.
- [ ] Confirm `.env.example` keeps `KOKORO_TTS_ASSET_DIR=C:\AskChipAssets\kokoro` and blank local runner command placeholders for `KOKORO_TTS_RUN_COMMAND` and `FASTER_WHISPER_RUN_COMMAND`.
- [ ] Confirm no OpenAI/cloud/provider vars are added to `.env.example`.
- [ ] Confirm local venv setup is documented from repo root:
  ```powershell
  py -m venv .venv-local-runtime
  .\.venv-local-runtime\Scripts\activate
  python -m pip install --upgrade pip setuptools wheel
  pip uninstall -y onnxruntime onnxruntime-gpu
  pip install onnxruntime-gpu kokoro-onnx faster-whisper
  ```
- [ ] Confirm ONNX Runtime provider validation references `CUDAExecutionProvider` and `CPUExecutionProvider`.
- [ ] Confirm Kokoro/faster-whisper import checks are documented.
- [ ] Confirm docs explicitly state package installation alone is insufficient and AskChappy still requires local HTTP services:
  - Kokoro TTS: `http://127.0.0.1:8880`
  - faster-whisper STT: `http://127.0.0.1:8890`
- [ ] Confirm local model/audio assets remain out of git (example: `C:\AskChipAssets\kokoro\kokoro-v1.0.onnx`, `C:\AskChipAssets\kokoro\voices-v1.0.bin`).

## Phase 21D wrapper checklist additions
- [ ] Confirm `services/local-runtime/kokoro_tts_server.py` exists and is used by default `KOKORO_TTS_RUN_COMMAND` template.
- [ ] Confirm `services/local-runtime/faster_whisper_stt_server.py` exists and is used by default `FASTER_WHISPER_RUN_COMMAND` template.
- [ ] Confirm `services/local-runtime/requirements.txt` exists and includes only local runtime packages.
- [ ] Confirm local wrapper setup instructions include:
  - venv creation (`py -m venv .venv-local-runtime`)
  - `pip install -r services/local-runtime/requirements.txt`
  - ONNX provider check for `CUDAExecutionProvider`
- [ ] Confirm documented startup/validation commands:
  - `./scripts/start-kokoro-tts.ps1`
  - `./scripts/start-faster-whisper-stt.ps1`
  - `./scripts/check-local-runtime.ps1`
  - `./scripts/start-local-runtime.ps1`

## Phase 22 visual UX checks
- [ ] Confirm room fits entirely within viewport during `/chappy/session/:sessionId` (no page-level vertical scroll).
- [ ] Confirm only transcript/chat message area scrolls during active conversation growth.
- [ ] Confirm bottom toolbar remains visible and usable without page scrolling.
- [ ] Confirm guided modes/current mode panel does not force page scroll (compact and/or internally scrollable).
- [ ] Confirm modern login modal is present on `/chappy`.
- [ ] Confirm no raw browser-default scaffold appearance remains in main AskChappy flow.
- [ ] Confirm Zoom-like room layout is present on `/chappy/session/:sessionId`.
- [ ] Confirm admin controls are hidden from normal user sessions.
- [ ] Confirm runtime readiness UI is compact/non-dominant.
- [ ] Confirm entry page/guided modes contain no stale scaffold wording.

- [ ] Confirm mic is PTT/Mic based and avoids recorder terminology.
- [ ] Confirm /chappy is a pre-call lobby, not landing-page scaffold wording.
- [ ] Confirm session is a Zoom-like room with Chappy stage as focal participant.

- [x] `/chappy` is a pre-call lobby.
- [x] Session uses Zoom-room layout.
- [x] Chappy stage is dominant.
- [x] Persistent bottom toolbar is present.
- [x] No recorder terminology in normal session.
- [x] No stage debug metadata in normal session.
- [x] Runtime readiness is compact.
- [x] Admin controls hidden from normal session.
