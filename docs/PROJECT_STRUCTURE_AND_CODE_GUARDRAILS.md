# AskChappy Project Structure and Code Guardrails

## 1. Purpose

This document prevents file bloat, duplicate contracts, route drift, and tangled implementation as AskChappy moves from documentation into code.

Future implementation PRs must keep files focused, modules clear, and contracts centralized.

## 2. Target repository structure

Intended first implementation structure:

```text
apps/
  askchappy-ui/
    src/
      app/
      routes/
      chappy/
      admin/
      session/
      transcript/
      modes/
      auth/
      voice/
      avatar/
      summary/
      components/
      styles/
      tests/

services/
  askchappy-api/
    src/
      api/
      sessions/
      transcript/
      modes/
      auth/
      voice/
      summary/
      contracts/
      events/
      tests/

shared/
  contracts/
    askchappy.ts
    transcript.ts
    modes.ts
    session.ts
    auth.ts
    voice.ts

docs/
```

Guardrail intent:
- `apps/askchappy-ui` owns web UX, route handling, and client state.
- `services/askchappy-api` owns API/runtime orchestration and event handling.
- `shared/contracts` is the single home for shared type contracts used by both UI and API.
- `docs/` remains the normative source for architecture and product decisions.

## 3. File size and module boundary guardrails

### 3.1 File size guardrails
- Target file size: under **350 lines**.
- Soft warning: over **450 lines**.
- Hard warning: over **600 lines**.
- Must split: over **800 lines** unless there is a documented reason.
- Test files may exceed **800 lines** when they are table-driven, readable, and organized clearly.
- Generated files are exempt, but generated files should not be committed unless intentionally required.

Guardrail interpretation rules:
- File size alone is not the only concern; mixed responsibility is the real problem.
- A focused **500-line** file is acceptable.
- A **250-line** file that mixes route handling, state, API calls, rendering, persistence, and business logic is not acceptable.
- A route/page file should mostly compose smaller components.
- A component file should not also own API calls, persistence, mode logic, and styling complexity.
- A service file should not mix validation, persistence, prompt behavior, and HTTP routing.
- If a file is growing because it owns multiple concerns, split it before adding more behavior.

### 3.2 Split triggers
Split a file immediately if any of the following are true:
- It mixes route definitions and business logic.
- It mixes transcript persistence with UI rendering logic.
- It contains multiple unrelated domain responsibilities.
- It duplicates contract/type definitions already present in `shared/contracts`.

### 3.3 Anti-god-file policy
- Do not create “everything” modules such as `app.tsx`/`server.ts` that centralize unrelated logic.
- Prefer small composition roots that wire focused modules.
- Keep domain logic in domain folders (`session/`, `transcript/`, `modes/`, `auth/`, `voice/`, `summary/`).

## 4. Contract centralization guardrails

- Shared contracts must be defined once in `shared/contracts/*` and imported everywhere else.
- Do not redefine `session_mode`, transcript message shape, auth role enum, or voice profile lifecycle in multiple places.
- `docs/IMPLEMENTATION_CONTRACTS.md` is normative for behavior; `shared/contracts` is normative for code-level shared types.
- Any contract change must update both docs and shared contract files in the same PR.

## 5. Route drift and stale-route prevention

- Active UI routes must remain aligned to `docs/IMPLEMENTATION_CONTRACTS.md`.
- Retired routes (`/demo*`, `/visual-session*`) must not return as active UX routes.
- Add a route inventory check in implementation PRs (UI routes and API endpoints touched).
- If a new route is added, update docs/contract references in the same PR.

## 6. Duplication and coupling guardrails

- Do not copy transcript transformation logic across UI/API modules; extract shared helpers.
- Keep UI components presentation-focused; runtime rules belong in session/transcript/mode modules.
- Keep auth role checks centralized (no scattered role-string conditionals).
- Keep event model separate from transcript-visible messages.

## 7. PR checklist for implementation-phase changes

Implementation PRs should confirm:
- File/module sizes are reviewed against tiered thresholds (target <350, soft warning >450, hard warning >600, split required >800 unless justified).
- No duplicate contract declarations were introduced.
- Route map changes are documented and contract-aligned.
- Transcript/event separation is preserved.
- New modules follow target folder boundaries.

## 8. Exceptions process

If a file must exceed limits temporarily:
1. Add a short "why now" explanation in PR description.
2. Add a follow-up split task in the PR checklist.
3. Avoid adding more unrelated logic to that file until split is completed.

Exceptions are temporary and should not become default architecture practice.
