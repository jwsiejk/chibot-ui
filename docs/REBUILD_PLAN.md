# AskChappy Rebuild Plan

- Execute implementation phases using `docs/BUILD_PLAYBOOK.md` as the phase-by-phase prompt and verification guide.

## Phase 0 — Docs-only cleanup
- Remove stale app/runtime code and outdated product framing.
- Preserve essential lessons as reference docs only.

## Phase 1 — New app skeleton
- Create fresh repository/app structure for AskChappy implementation.
- Define clean module boundaries for UI, session, transcript, and runtime adapters.
- Enforce `docs/IMPLEMENTATION_CONTRACTS.md` as required scaffold input before coding routes/runtime behavior.

## Phase 2 — Sessions and canonical transcript
- Implement transcript-first session engine.
- Enforce `text`/`role`/`source` canonical contract.

## Phase 3 — Zoom-like Chappy UI
- Build stage-first layout with Chappy-centered session experience.
- Integrate transcript/chat panel using canonical transcript source.

## Phase 4 — Open Q&A persona/runtime
- Ship default Open Q&A flow.
- Apply Chappy persona behavior layer for DDN partner enablement.

## Phase 5 — Guided modes
- Implement guided mode overlays (not separate bots).
- Ensure in-session switching between Open Q&A and guided overlays.

## Phase 6 — Voice input/output
- Add speech input and TTS output over shared transcript pipeline.
- Ensure no modality bypasses canonical transcript.

## Phase 7 — Chappy voice clone integration
- Integrate consented Chappy voice provider through TTS abstraction.
- Keep voice assets outside public repository.

## Phase 8 — Chappy avatar integration
- Progress from placeholder to richer avatar states.
- Add future-ready hooks for speaking/viseme animation.

## Phase 9 — Partner recap/summary
- Generate session summaries, action items, and talk tracks.
- Ground outputs strictly in transcript + session metadata.

## Phase 10 — Packaging/deployment
- Define deployment targets, configuration profiles, and release workflows.
- Add production hardening and operational checks.

## Phase 17 — Assistant/model runtime scaffold (knowledge-first)
- Implement first usable assistant conversation/runtime path using local open-source LLM knowledge via a local Ollama runtime (default model: `gemma3:4b`).
- Keep local-first production framing and canonical transcript/session contracts.
- Runtime target for this phase is local Ollama only (`OLLAMA_BASE_URL` default `http://127.0.0.1:11434`; `OLLAMA_MODEL` default `gemma3:4b`; optional `OLLAMA_KEEP_ALIVE=30m`; optional `OLLAMA_NUM_CTX=8192`).
- No OpenAI runtime, no hosted/cloud LLM SDK, and no cloud LLM API keys in this phase.
- Missing Ollama runtime or missing local model must yield a clear local runtime not-configured state (no fake assistant intelligence).
- Do not require DDN document upload, ingestion, embeddings, vector search, or RAG before shipping this phase.

## Phase 18 — Content grounding / document ingestion / RAG (required later, deferred now)
- Add DDN-specific content grounding workflows after Phase 17 runtime is established.
- Include proprietary DDN content bundle handling, file ingestion/upload, embeddings, vector retrieval/search, and knowledge-base lifecycle management.
- Treat this phase as required follow-on scope, but not a blocker for first usable assistant/model runtime.

## Phase 19 — STT and browser microphone input
- Add STT/browser microphone runtime unless project direction changes.
- Preserve transcript-first guarantees and avoid modality-specific transcript forks.

## Phase 1.5 — MVP login, roles, and admin surface
- Add lightweight email-only login modal to `/chappy` for local/demo role selection.
- Establish two roles: `standard_user` and `admin` (`jsiejk@ddn.com` initial admin).
- Add hidden admin route scaffold (`/admin`, `/admin/voice`, `/admin/avatar`) with simple not-authorized state for standard users.

## Phase 7.5 — Admin Voice Studio workflow
- Implement admin-only Voice Studio workflow outside normal user sessions.
- Support voice profile lifecycle: `draft`, `testing`, `approved`, `published`, `disabled`.
- Ensure `/chappy/session/:sessionId` only consumes currently published voice profile.
- Keep consent handling lightweight for MVP docs intent.

## Phase 0.5 — Structure guardrails before coding
- Adopt `docs/PROJECT_STRUCTURE_AND_CODE_GUARDRAILS.md` as mandatory implementation scaffold policy.
- Lock intended UI/API/shared folder boundaries before feature work starts.
- Enforce anti-bloat file-size and anti-duplication rules from first code PR onward.
