# Codex / GitHub Bot Workflow for AskChappy

## Branch and baseline discipline
- Start every task from the current `main` branch baseline.
- Clearly separate planning/docs changes from implementation changes.

## Contract preservation
- Preserve documented contracts (especially canonical transcript semantics).
- Do not reintroduce retired AskChip/Expert Desk/VMware runtime concepts as active product behavior.

## Implementation standards (when coding begins)
- Do not use mocks/fake integrations unless explicitly requested.
- Deliver production-ready code for implemented scope.
- Run tests/lints before delivery once code exists.
- Include test/lint command output in PR summaries.

## Documentation discipline
- Update architecture/product docs whenever core behavior or contracts change.
- Keep decisions discoverable in architecture decision notes.


## Implementation contract gate
- Before any implementation PR, confirm compliance with `docs/IMPLEMENTATION_CONTRACTS.md`.
- Reject PRs that reintroduce retired `/demo*` or `/visual-session*` primary routes.
- Treat contract drift as a blocking architecture issue, not a minor doc mismatch.

- Future implementation PRs must keep conversational transcript messages separate from internal session events.
- Do not satisfy audit/diagnostic needs by injecting hidden app telemetry into the visible chat transcript.

## MVP auth/admin guardrails (documentation + future implementation)
- Preserve the lightweight MVP login model: email-only on `/chappy`, no password, local/demo scope only.
- Preserve fixed initial admin mapping: `jsiejk@ddn.com` is `admin`, all other emails are `standard_user` unless a later ADR changes this.
- Keep admin controls hidden from standard user navigation.
- Keep voice cloning/profile management controls out of normal `/chappy/session/:sessionId` UX.
- Treat admin-only Voice Studio as UX-governance boundary, not heavy security-hardening work for MVP.
- Do not over-engineer consent/legal workflow in MVP implementation unless scope is explicitly expanded.

## Project structure and anti-bloat gate
- Before implementation PR approval, validate against `docs/PROJECT_STRUCTURE_AND_CODE_GUARDRAILS.md`.
- Block PRs that introduce god files, duplicate contracts, or route drift from canonical maps.
- Require module splits when files exceed guardrail thresholds without explicit exception rationale.
