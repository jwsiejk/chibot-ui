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
- `vite`: local-first React runtime server + production-style build output for AskChappy app shell verification.

## Local runtime tooling note (Phase 14 fix)
- Phase 14 local start/dev now uses `vite` as the real local runtime for the AskChappy React/router scaffold.
- This replaces the retired plain-text local runtime server behavior and enables app-shell serving, history fallback, and noninteractive production-style local build checks.

## Why these dependencies are present
- The dependency set is intentionally minimal for local-first production scaffolding.
- Dependencies support typed contract enforcement, route/runtime tests, lint checks, and UI component validation.
- No dependency exists solely for speculative future cloud features.

## Explicit exclusions retained in Phase 13
- No cloud voice/model SDK dependencies were added.
- No OpenAI runtime dependency was added.
- No hosted/cloud LLM SDK dependency was added.
- No DDN document ingestion/upload dependency was added.
- No embeddings/vector database/RAG dependency was added.
- No real voice/avatar asset dependencies were added.
- No database dependency was added.
- No STT/microphone runtime dependencies were added.

## Package lockfile status
- No `package-lock.json` is currently tracked in this repository.
- Phase 14 cleanup does not introduce a new package manager policy or lockfile convention.

- Phase 15 keeps existing dependency boundaries: no cloned provider SDK added, no cloud voice SDK added, and standard voice remains the only active synthesis runtime.
- Planned near-term runtime sequencing remains: Phase 17 uses local open-source LLM knowledge first via local Ollama runtime (`gemma3:4b` default); DDN content grounding and RAG dependencies are deferred to a later required phase.


## Runtime direction exclusions (roadmap policy)
- Phase 17 target runtime remains local Ollama with default `gemma3:4b` at `http://127.0.0.1:11434`.
- Standard local TTS direction remains Kokoro/kokoro-onnx; future STT direction remains faster-whisper.
- No OpenAI/cloud LLM runtime, hosted model SDK, cloud voice SDK, or cloud TTS dependency should be introduced without a future ADR change.


## Phase 18 dependency check
- Local Kokoro/kokoro-onnx TTS uses native `fetch` + local HTTP endpoint only.
- No cloud voice SDK dependency was added.
- No additional TTS npm dependency was added for Phase 18.
