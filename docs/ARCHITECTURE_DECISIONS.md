# AskChappy Architecture Decisions

## ADR-001: Clean rebuild instead of refactor
Decision: Retire legacy implementation and restart from an explicit AskChappy V1 architecture.

## ADR-002: Repository remains documentation-only after cleanup
Decision: Post-cleanup repo state is planning/reference only; no active runtime code.

## ADR-003: Default interaction path is Open Q&A
Decision: Sessions begin in Open Q&A by default; guided modes are optional overlays.

## ADR-004: Modes are metadata/behavior overlays, not separate bots
Decision: One Chappy identity/runtime with mode context applied via session metadata.

## ADR-005: Text/chat/voice share one canonical transcript
Decision: All modalities map to a single message model using `text`, `role`, `source`.

## ADR-006: Persona is separate from runtime engine
Decision: Chappy persona behavior remains decoupled from underlying model/provider runtime.

## ADR-007: Voice clone assets remain private
Decision: Source recordings and trained voice artifacts are private, consent-gated assets outside public repo.

## ADR-008: Avatar assets are replaceable and private-friendly
Decision: Avatar system supports placeholder-to-advanced evolution while keeping private likeness assets external.

## ADR-009: No stale Expert Desk / VMware app code in active repository
Decision: Remove outdated routes, services, and runtime files that imply legacy product continuity.


## ADR-010: Implementation contracts are normative for V1 build
Decision: `docs/IMPLEMENTATION_CONTRACTS.md` is the required contract source for route map, metadata shape, transcript schema, session state model, and recap behavior.

## ADR-012: Separate conversational transcript from internal session events
Decision: AskChappy keeps one canonical transcript for conversational content, while internal app-state changes such as mode switches, validation corrections, diagnostics, and lifecycle telemetry are recorded as session events or metadata events. Visible transcript messages are only for user/assistant/system content intended to be shown or spoken.

Consequences:
- Chat/transcript stays clean and human-readable.
- Mode changes remain auditable.
- Recaps can use both transcript and session events while clearly distinguishing them.
- The app must not create fake user or assistant messages to represent hidden state changes.

## ADR-013: MVP login is email-only and local/demo scoped
Decision: AskChappy V1 starts with an email-only login modal on `/chappy` with no password. Email is used only for demo/local role selection and personalization, and is not production authentication.

## ADR-014: Two-role MVP with fixed initial admin
Decision: V1 MVP role model is `standard_user` and `admin`, with `jsiejk@ddn.com` as initial admin and all other emails as standard users.

## ADR-015: Admin-only Voice Studio for UX governance
Decision: Voice Studio is restricted to admin routes (`/admin/voice`) so normal users cannot alter the shared Chappy voice experience. This is primarily product/UX governance, not a heavy security-hardening boundary.

## ADR-016: Voice profile lifecycle and publish model
Decision: Chappy voice profiles follow `draft -> testing -> approved -> published -> disabled`. Normal user sessions only consume the currently published voice profile; cloning workflows occur outside standard `/chappy/session/:sessionId` sessions.

## ADR-017: MVP consent workflow remains lightweight
Decision: MVP requires lightweight admin confirmation that Chapman approved voice usage (for example: “I confirm Chapman approved using this voice for AskChappy.”), without heavy legal/security workflow implementation in docs scope.

## ADR-018: Project structure and anti-bloat guardrails are mandatory
Decision: AskChappy implementation must follow `docs/PROJECT_STRUCTURE_AND_CODE_GUARDRAILS.md`, including repository structure targets, file-size thresholds, split triggers, contract centralization, and stale-route prevention.

Consequences:
- Reduces risk of oversized, tangled modules as implementation begins.
- Keeps shared contracts centralized and versioned intentionally.
- Prevents route drift and accidental reintroduction of retired flows.


## ADR-019: Cloned voice readiness gate precedes provider adapter integration
Decision: Phase 13 adds a cloned voice configuration contract and readiness gate only; synthesis remains on the standard voice path until a future approved provider adapter is implemented and prerequisites are available (provider, config, consent, audio/publication readiness).

Consequences:
- Prevents premature claims that cloned voice is live.
- Keeps local-first runtime stable with standard voice default.
- Preserves asset/privacy boundaries by avoiding committed private voice artifacts.
