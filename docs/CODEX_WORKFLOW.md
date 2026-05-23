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
