# BUILD 08 — Cutover & CI Guards

**Alignment guard (do not omit):**
- This build MUST align with the SSOT docs in `/docs` and all previous builds.
- Do not touch files outside the listed scope.
- Do not rename routes, env vars, or policy keys.
- Every new/changed file ≤ 500 lines. Keep changes small and isolated.
- Preserve the `chat.v2` contract and include the telemetry block in policy frames.

## Tasks
### B8-A: Single-path v2
**Files:** app/asgi_gateway.py
**Non-goals:** No v1 fallback.
**Acceptance:**
- `/ws/v1/chat` returns 410 Gone with JSON error; `/ws/v2/chat` works.

### B8-B: CI checks
**Files:** ops/ci_checks.md
**Non-goals:** No external CI setup—just docs/script stub.
**Acceptance:**
- Checklist explains failing PRs if `/ws/v1/chat` referenced or top-level `/templates` added.

