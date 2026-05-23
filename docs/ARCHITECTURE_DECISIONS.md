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
