# AskChappy Dependency Review (Phase 14)

## Direct dependencies
- `react`: UI rendering for AskChappy pages/components.
- `react-dom`: browser DOM renderer for React UI.
- `react-router-dom`: route definitions and navigation for canonical AskChappy/admin routes.

## Dev dependencies
- `typescript`: static type-checking for shared contracts, UI, and API scaffolding.
- `vitest`: test runner for UI and API contract/runtime tests.
- `jsdom`: browser-like test environment for UI tests.
- `@testing-library/react`: React component testing utilities.
- `@testing-library/jest-dom`: custom DOM assertions for test readability.
- `eslint`: lint framework.
- `@eslint/js`: base JavaScript ESLint rules.
- `typescript-eslint`: TypeScript-aware linting.
- `globals`: known global definitions for browser/node lint environments.
- `@types/react`: React TypeScript types.
- `@types/react-dom`: React DOM TypeScript types.

## Local runtime tooling note (Phase 14)
- Phase 14 local start/dev uses existing `typescript` + a tiny Node static server script (`scripts/local-runtime-server.mjs`) to avoid adding cloud/runtime dependencies.

## Why these dependencies are present
- The dependency set is intentionally minimal for local-first production scaffolding.
- Dependencies support typed contract enforcement, route/runtime tests, lint checks, and UI component validation.
- No dependency exists solely for speculative future cloud features.

## Explicit exclusions retained in Phase 13
- No cloud voice/model SDK dependencies were added.
- No OpenAI/model runtime dependency was added.
- No real voice/avatar asset dependencies were added.
- No database dependency was added.
- No STT/microphone runtime dependencies were added.
