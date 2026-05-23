# AskChappy Build Playbook

## 1. Purpose

This document is the execution guide for building AskChappy with Codex/GitHub bot prompts. Each phase must be completed, reviewed, tested, and merged before moving to the next phase.

The build must remain:
- contract-first
- small PR based
- test-backed
- free of stale AskChip/Expert Desk/VMware active code
- aligned to `docs/IMPLEMENTATION_CONTRACTS.md`
- aligned to `docs/PROJECT_STRUCTURE_AND_CODE_GUARDRAILS.md`

## 2. Global rules for every build phase

Every future implementation prompt must include these rules:

- Start from current `main`.
- Follow `docs/IMPLEMENTATION_CONTRACTS.md`.
- Follow `docs/PROJECT_STRUCTURE_AND_CODE_GUARDRAILS.md`.
- Do not reintroduce retired `/demo*` or `/visual-session*` routes.
- Do not use `content` as transcript field; use `text`.
- Do not create separate voice/chat transcript models.
- Do not expose Voice Studio controls to standard users.
- Do not implement features outside the current phase.
- Do not block the first usable assistant/model runtime phase on DDN content grounding or document ingestion.
- Keep files focused and under guardrail thresholds.
- Add tests in the same PR.
- Run tests/lints and include command output in PR summary.
- Update docs if contracts or architecture change.

Roadmap guardrail:
- Phase 17 should ship assistant/model runtime scaffold with local open-source LLM knowledge first via local Ollama runtime (`gemma3:4b` default).
- Phase 18 (DDN document upload/content grounding/embeddings/RAG/vector search/knowledge-base workflows) is explicitly deferred in near-term execution and tracked as required later work.
- Phase 19 remains STT/browser microphone input unless direction changes.

## 3. Required PR summary format

Each implementation PR summary must include:

```text
## Summary
- ...

## Contract compliance
- Routes:
- Transcript:
- Metadata:
- Modes:
- Auth/Admin:
- File structure:

## Tests / verification
Commands run:
- ...

Output:
- ...

## Not included
- ...
```

## 4. Phase list

### Phase 1 — App skeleton and route scaffold

**Goal:**  
Create the minimal frontend/backend/shared structure and route scaffold.

**Allowed:**
- `apps/askchappy-ui`
- `services/askchappy-api`
- `shared/contracts`
- basic route placeholders
- `/`, `/chappy`, `/chappy/session/:sessionId`, `/chappy/summary/:sessionId`, `/dev`, `/admin`, `/admin/voice`, `/admin/avatar`
- route tests
- retired route absence tests

**Not allowed:**
- real voice cloning
- STT/TTS implementation
- real avatar
- RAG
- DDN content ingestion
- database
- old AskChip/Expert Desk routes

**Required tests:**
- route map tests
- retired route absence tests
- basic app render tests

**Exact Codex prompt for Phase 1:**
```text
You are working in repository jwsiejk/chibot-ui.
Implement Phase 1 only: app skeleton and route scaffold.

Build:
- minimal structure for:
  - apps/askchappy-ui
  - services/askchappy-api
  - shared/contracts
- route placeholders for:
  - /
  - /chappy
  - /chappy/session/:sessionId
  - /chappy/summary/:sessionId
  - /dev
  - /admin
  - /admin/voice
  - /admin/avatar
- route map tests
- retired route absence tests
- basic app render tests

Do not build:
- real voice cloning
- STT/TTS
- avatar implementation beyond placeholder route/page text
- RAG
- DDN content ingestion
- database wiring
- old AskChip/Expert Desk/VMware routes or runtime behavior

Must follow:
- docs/IMPLEMENTATION_CONTRACTS.md
- docs/PROJECT_STRUCTURE_AND_CODE_GUARDRAILS.md

Rules:
- Do not reintroduce /demo* or /visual-session* routes.
- Keep files focused and under guardrail thresholds.
- Add tests in same PR.
- Run tests/lints and include command output in PR summary.

PR summary must use required format in docs/BUILD_PLAYBOOK.md section 3.
Include a clear "Not included" section listing intentionally deferred scope.
```

### Phase 2 — Shared contracts and validation

**Goal:**  
Implement shared TypeScript contracts/enums for transcript, session, modes, auth, voice profile lifecycle, and routes.

**Allowed:**
- `shared/contracts/*`
- validation helpers
- unit tests

**Required contracts:**
- transcript message
- session metadata
- session state
- mode enum
- auth role enum
- voice lifecycle enum
- route constants

**Required tests:**
- mode enum validation
- role validation
- transcript field validation
- no content transcript field

**Exact Codex prompt for Phase 2:**
```text
You are working in repository jwsiejk/chibot-ui.
Implement Phase 2 only: shared contracts and validation.

Build:
- shared TypeScript contracts/enums in shared/contracts for:
  - transcript message
  - session metadata
  - session state
  - mode enum
  - auth role enum
  - voice lifecycle enum
  - route constants
- validation helpers
- unit tests

Required tests:
- mode enum validation
- role validation
- transcript field validation
- explicit test proving transcript model does not use content field (text only)

Do not build:
- UI feature expansion outside contract wiring
- runtime voice/avatar integrations
- database integrations

Must follow:
- docs/IMPLEMENTATION_CONTRACTS.md
- docs/PROJECT_STRUCTURE_AND_CODE_GUARDRAILS.md

Rules:
- Use text, never content, for transcript message body.
- Do not create separate voice/chat transcript models.
- Keep modules focused and under guardrails.
- Add tests in same PR.
- Run tests/lints and include command output in PR summary.

PR summary must use required format in docs/BUILD_PLAYBOOK.md section 3.
```

### Phase 3 — MVP email login and role model

**Goal:**  
Implement email-only MVP login and role detection.

**Rules:**
- `jsiejk@ddn.com => admin`
- all other emails => `standard_user`
- no password
- local-first only label visible in docs/comments/UI copy where appropriate

**Required tests:**
- admin role mapping
- standard user mapping
- login modal render
- admin nav hidden for standard user

**Exact Codex prompt for Phase 3:**
```text
You are working in repository jwsiejk/chibot-ui.
Implement Phase 3 only: MVP email login and role model.

Build:
- email-only login flow on /chappy entry
- role mapping:
  - jsiejk@ddn.com => admin
  - all other emails => standard_user
- no password flow
- local-first or local-only labeling in docs/comments/UI copy where appropriate
- role-aware nav visibility (hide admin nav for standard_user)

Required tests:
- admin role mapping
- standard user mapping
- login modal render
- admin nav hidden for standard user

Do not build:
- OAuth/SSO
- production auth hardening
- admin features outside role gating scaffold

Must follow:
- docs/IMPLEMENTATION_CONTRACTS.md
- docs/PROJECT_STRUCTURE_AND_CODE_GUARDRAILS.md

Rules:
- Keep auth model intentionally minimal for MVP docs contract.
- Keep files small and focused.
- Add tests in same PR.
- Run tests/lints and include command output in PR summary.

PR summary must use required format in docs/BUILD_PLAYBOOK.md section 3.
```

### Phase 4 — Session and transcript engine

**Goal:**  
Implement minimal real local session/transcript behavior.

**Allowed:**
- create session
- load session
- append user message
- append assistant placeholder/echo response only if clearly local scaffold behavior
- transcript read
- `metadata.askchappy` default

**Important:**
No fake claims of AI intelligence yet. If assistant response is placeholder, label implementation as scaffold.

**Required tests:**
- session creation includes `metadata.askchappy`
- transcript uses `text`
- typed input appends user message
- session events separate from transcript

**Exact Codex prompt for Phase 4:**
```text
You are working in repository jwsiejk/chibot-ui.
Implement Phase 4 only: session and transcript engine.

Build:
- local session creation/load
- transcript append pipeline for user messages
- optional assistant placeholder/echo response only when clearly labeled as local scaffold behavior
- transcript read behavior
- default metadata.askchappy on session creation
- session event tracking separate from transcript messages

Required tests:
- session creation includes metadata.askchappy
- transcript uses text field
- typed input appends user message
- session events are separate from transcript

Do not build:
- claims of real AI intelligence
- external model integrations
- voice/avatar integrations

Must follow:
- docs/IMPLEMENTATION_CONTRACTS.md
- docs/PROJECT_STRUCTURE_AND_CODE_GUARDRAILS.md

Rules:
- Never use content field for transcript.
- Keep session events out of visible transcript unless intentionally user-visible.
- Keep files focused and small.
- Add tests in same PR.
- Run tests/lints and include command output in PR summary.

PR summary must use required format in docs/BUILD_PLAYBOOK.md section 3.
```

### Phase 5 — Chappy entry and Zoom-like session UI

**Goal:**  
Build the first real Chappy user experience.

**Allowed:**
- `/chappy` entry screen
- Chappy stage placeholder
- session state indicator
- mode cards
- start Open Q&A
- `/chappy/session/:sessionId` shell
- transcript panel
- typed input
- right rail

**Not allowed:**
- voice implementation
- avatar implementation beyond placeholder

**Required tests:**
- entry screen render
- start session flow
- session page render
- transcript panel render
- current mode visible

**Exact Codex prompt for Phase 5:**
```text
You are working in repository jwsiejk/chibot-ui.
Implement Phase 5 only: Chappy entry and Zoom-like session UI.

Build:
- /chappy entry experience
- Chappy stage placeholder
- session state indicator
- mode cards and start Open Q&A action
- /chappy/session/:sessionId page shell
- transcript panel
- typed input
- right rail scaffold

Required tests:
- entry screen render
- start session flow
- session page render
- transcript panel render
- current mode visible

Do not build:
- voice runtime implementation
- avatar implementation beyond placeholder UI

Must follow:
- docs/IMPLEMENTATION_CONTRACTS.md
- docs/PROJECT_STRUCTURE_AND_CODE_GUARDRAILS.md

Rules:
- Preserve canonical transcript semantics.
- Keep UI modules focused under guardrails.
- Add tests in same PR.
- Run tests/lints and include command output in PR summary.

PR summary must use required format in docs/BUILD_PLAYBOOK.md section 3.
```

### Phase 6 — Guided modes and mode switching

**Goal:**  
Implement guided mode enum, right rail behavior, and mode switching as metadata/session events.

**Rules:**
- mode switch preserves session id
- mode switch preserves transcript
- mode switch does not create visible chat message by default
- optional assistant acknowledgment is allowed only if deliberately emitted

**Required tests:**
- mode switch updates metadata
- mode switch creates session event
- transcript not polluted by hidden mode event
- right rail updates by mode

**Exact Codex prompt for Phase 6:**
```text
You are working in repository jwsiejk/chibot-ui.
Implement Phase 6 only: guided modes and mode switching.

Build:
- guided mode enum usage in session UX
- right rail behavior updates by active mode
- mode switching stored as metadata/session events

Rules to enforce:
- mode switch preserves session id
- mode switch preserves transcript
- mode switch does not create visible chat message by default
- optional assistant acknowledgment only if deliberately emitted

Required tests:
- mode switch updates metadata
- mode switch creates session event
- transcript not polluted by hidden mode events
- right rail updates by mode

Do not build:
- unrelated new features
- voice/avatar integrations

Must follow:
- docs/IMPLEMENTATION_CONTRACTS.md
- docs/PROJECT_STRUCTURE_AND_CODE_GUARDRAILS.md

Rules:
- Keep transcript canonical and clean.
- Keep file/module boundaries disciplined.
- Add tests in same PR.
- Run tests/lints and include command output in PR summary.

PR summary must use required format in docs/BUILD_PLAYBOOK.md section 3.
```

### Phase 7 — Summary / partner recap

**Goal:**  
Implement `/chappy/summary/:sessionId`.

**Summary should use:**
- canonical transcript
- session metadata
- mode history/session events

**Outputs:**
- notes
- action items
- talk track
- follow-up draft placeholder if enough transcript context exists

**Required tests:**
- summary route loads
- summary grounded in transcript text
- session events distinguished from spoken/displayed transcript
- no support-ticket handoff framing

**Exact Codex prompt for Phase 7:**
```text
You are working in repository jwsiejk/chibot-ui.
Implement Phase 7 only: summary / partner recap.

Build:
- /chappy/summary/:sessionId route
- summary generation pipeline grounded in:
  - canonical transcript
  - session metadata
  - mode history/session events
- outputs for notes, action items, talk track, and follow-up draft placeholder when enough transcript context exists

Required tests:
- summary route loads
- summary grounded in transcript text
- session events are distinguished from spoken/displayed transcript
- no support-ticket handoff framing

Do not build:
- unrelated chatbot or ticketing workflows
- features outside recap scope

Must follow:
- docs/IMPLEMENTATION_CONTRACTS.md
- docs/PROJECT_STRUCTURE_AND_CODE_GUARDRAILS.md

Rules:
- Keep transcript as source of truth.
- Keep session events separate from visible conversation.
- Add tests in same PR.
- Run tests/lints and include command output in PR summary.

PR summary must use required format in docs/BUILD_PLAYBOOK.md section 3.
```

### Phase 8 — Admin placeholder and Voice Studio shell

**Goal:**  
Implement admin-only placeholder routes and Voice Studio shell.

**Allowed:**
- `/admin`
- `/admin/voice`
- `/admin/avatar`
- voice profile lifecycle placeholder
- published/fallback voice status display

**Not allowed:**
- real voice cloning
- real model artifacts
- audio upload persistence unless explicitly scoped

**Required tests:**
- standard user blocked from admin routes
- admin can access admin routes
- Voice Studio controls absent from normal session
- voice profile lifecycle enum used from shared contract

**Exact Codex prompt for Phase 8:**
```text
You are working in repository jwsiejk/chibot-ui.
Implement Phase 8 only: admin placeholder and Voice Studio shell.

Build:
- admin-only placeholders for:
  - /admin
  - /admin/voice
  - /admin/avatar
- Voice Studio shell using shared voice lifecycle enum
- published/fallback voice status display

Required tests:
- standard user blocked from admin routes
- admin can access admin routes
- Voice Studio controls absent from normal session
- voice profile lifecycle enum sourced from shared contracts

Do not build:
- real voice cloning
- real model artifacts
- audio upload persistence unless explicitly scoped

Must follow:
- docs/IMPLEMENTATION_CONTRACTS.md
- docs/PROJECT_STRUCTURE_AND_CODE_GUARDRAILS.md

Rules:
- Maintain admin boundary and standard-user UX separation.
- Keep files focused and guardrail-compliant.
- Add tests in same PR.
- Run tests/lints and include command output in PR summary.

PR summary must use required format in docs/BUILD_PLAYBOOK.md section 3.
```

### Phase 9 — TTS provider interface and fallback voice

**Goal:**  
Implement TTS provider interface and fallback placeholder/local dev voice path.

**Rules:**
- TTS consumes assistant transcript `text`
- voice provider must not generate independent content
- no cloned voice yet

**Required tests:**
- TTS called with transcript text
- fallback behavior when no published voice profile
- no user-facing Voice Studio controls in session

**Exact Codex prompt for Phase 9:**
```text
You are working in repository jwsiejk/chibot-ui.
Implement Phase 9 only: TTS provider interface and fallback voice.

Build:
- TTS provider interface abstraction
- fallback placeholder/local-dev voice path
- wiring from assistant transcript text to TTS invocation

Rules to enforce:
- TTS consumes assistant transcript text
- voice provider must not generate independent content
- no cloned voice integration yet

Required tests:
- TTS called with transcript text
- fallback when no published voice profile
- no user-facing Voice Studio controls in session UX

Do not build:
- cloned voice provider integration
- unrelated audio systems

Must follow:
- docs/IMPLEMENTATION_CONTRACTS.md
- docs/PROJECT_STRUCTURE_AND_CODE_GUARDRAILS.md

Rules:
- Transcript text remains source of spoken content.
- Keep modules focused and under guardrails.
- Add tests in same PR.
- Run tests/lints and include command output in PR summary.

PR summary must use required format in docs/BUILD_PLAYBOOK.md section 3.
```

### Phase 10 — Chappy cloned voice integration

**Goal:**  
Integrate real Chappy voice provider only after voice sample/provider decision is ready.

**Rules:**
- no raw audio/model artifacts committed unless intentionally approved
- admin publishes active voice profile
- standard sessions use published profile
- fallback still works

**Required tests:**
- published profile selected
- disabled profile not selected
- fallback if no published profile
- transcript text remains source

**Exact Codex prompt for Phase 10:**
```text
You are working in repository jwsiejk/chibot-ui.
Implement Phase 10 only: Chappy cloned voice integration.

Prerequisite:
- proceed only after voice sample/provider decision is approved.

Build:
- integration with approved Chappy voice provider
- admin-published active voice profile selection
- standard sessions consume published profile
- fallback path remains functional

Rules to enforce:
- no raw audio/model artifacts committed unless intentionally approved
- transcript text remains the source for spoken output

Required tests:
- published profile selected
- disabled profile not selected
- fallback used when no published profile
- transcript text remains source

Do not build:
- unrelated session UX expansion
- admin bypasses for profile governance

Must follow:
- docs/IMPLEMENTATION_CONTRACTS.md
- docs/PROJECT_STRUCTURE_AND_CODE_GUARDRAILS.md

Rules:
- Preserve admin governance boundaries.
- Keep files focused and under guardrails.
- Add tests in same PR.
- Run tests/lints and include command output in PR summary.

PR summary must use required format in docs/BUILD_PLAYBOOK.md section 3.
```

### Phase 11 — Avatar placeholder to Chappy avatar

**Goal:**  
Implement avatar state system.

**Stages:**
- placeholder avatar
- state-aware avatar
- future real Chappy avatar
- future speaking animation

**Required tests:**
- avatar follows session state
- no avatar controls visible to standard users unless intended
- session still works without avatar asset

**Exact Codex prompt for Phase 11:**
```text
You are working in repository jwsiejk/chibot-ui.
Implement Phase 11 only: avatar placeholder to Chappy avatar state system.

Build:
- staged avatar system progression:
  - placeholder avatar
  - state-aware avatar behavior
  - hooks for future real Chappy avatar
  - hooks for future speaking animation

Required tests:
- avatar follows session state
- no avatar controls visible to standard users unless intended
- session still works without avatar asset

Do not build:
- unrelated AI/runtime features
- scope beyond avatar state system

Must follow:
- docs/IMPLEMENTATION_CONTRACTS.md
- docs/PROJECT_STRUCTURE_AND_CODE_GUARDRAILS.md

Rules:
- Maintain clean separation from normal user admin controls.
- Keep implementation modular and guardrail-compliant.
- Add tests in same PR.
- Run tests/lints and include command output in PR summary.

PR summary must use required format in docs/BUILD_PLAYBOOK.md section 3.
```

### Phase 12 — Production hardening / packaging

**Goal:**  
Prepare for stable local-first deployment.

**Include:**
- environment config
- build scripts
- lint/test CI
- docs updates
- dependency review
- run guide

**Required tests:**
- full test suite
- build passes
- route tests
- contract tests

**Exact Codex prompt for Phase 12:**
```text
You are working in repository jwsiejk/chibot-ui.
Implement Phase 12 only: production hardening / packaging.

Build:
- environment configuration
- build scripts
- lint/test CI setup
- docs updates
- dependency review updates
- run guide for stable local-first deployment

Required tests:
- full test suite
- build passes
- route tests
- contract tests

Do not build:
- new product features outside hardening/packaging scope

Must follow:
- docs/IMPLEMENTATION_CONTRACTS.md
- docs/PROJECT_STRUCTURE_AND_CODE_GUARDRAILS.md

Rules:
- Preserve canonical routes/contracts during hardening.
- Keep files focused and avoid scope creep.
- Run tests/lints and include command output in PR summary.

PR summary must use required format in docs/BUILD_PLAYBOOK.md section 3.
```

## 5. Phase gate checklist

Before moving to next phase:
- PR merged
- tests/lints passed
- no stale routes
- docs updated
- no bloated files without justification
- no duplicate contracts
- no scope creep

## 6. Build prompt style

Every prompt must be explicit about:
- what to build
- what not to build
- files/modules expected
- contracts to preserve
- tests required
- PR summary requirements

## 7. First implementation phase pointer

The next implementation prompt should be **Phase 1 only: app skeleton and route scaffold**.  
Do not jump to voice, avatar, AI responses, or full session intelligence.


## 5. Current lock/state snapshot

- Phases 1–13 are completed.
- Phase 13 (cloned voice provider contract package + readiness gate) is complete and locked after cleanup commit `e0e183e`.
- Phase 13 did not add a real cloned voice provider adapter/runtime.
- Standard voice remains active/default until a future approved provider adapter is implemented.

### Proposed Phase 14 — Local-first start/dev runtime workflow

**Goal:** Define and implement a stable local-first app startup workflow (documentation-ready and implementation-ready).

**Scope (proposed):**
- Add dedicated local runtime start/dev command workflow.
- Keep canonical routes, transcript contracts, metadata rules, and admin boundaries unchanged.
- Preserve standard voice default behavior; cloned voice remains optional and gated.

**Not in scope:**
- Real cloned voice provider adapter integration unless all provider/config/audio/consent prerequisites are available and approved.
- Model runtime/STT/database/cloud/avatar-asset integrations.
