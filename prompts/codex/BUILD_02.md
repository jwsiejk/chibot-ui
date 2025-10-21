# BUILD 02 — Policy Manager (+ telemetry block)

**Alignment guard (do not omit):**
- This build MUST align with the SSOT docs in `/docs` and all previous builds.
- Do not touch files outside the listed scope.
- Do not rename routes, env vars, or policy keys.
- Every new/changed file ≤ 500 lines. Keep changes small and isolated.
- Preserve the `chat.v2` contract and include the telemetry block in policy frames.

## Tasks
### B2-A: Policy defaults incl. telemetry
**Files:** app/policy/loader.py, docs/10_CONTRACT_WS.md
**Non-goals:** No Engine wiring yet.
**Acceptance:**
- Loader snapshot includes keys: mode, allow_auto_vad, barge_in_enabled, auto_commit_when_ready, telemetry.
- Telemetry fields match spec (enabled, level, categories, redaction, sampling).

### B2-B: PolicyManager
**Files:** app/voice_v2/policy_manager.py
**Non-goals:** No ASR/TTS; no client work.
**Acceptance:**
- Effective ACWR = policy_state ∧ admin_switch; admin is a single boolean.
- Diff(prev→next) includes only changed keys (incl. telemetry).

### B2-C: Engine pushes policy.interaction
**Files:** app/voice_v2/engine.py, app/telemetry/exporter.py
**Non-goals:** No gate/ASR/TTS yet.
**Acceptance:**
- On open, exactly one `policy:applied` with diffs; telemetry echoed.
- Policy frames ALWAYS include the four keys + telemetry block.

### B2-D: ACWR recompute breadcrumb
**Files:** app/voice_v2/engine.py, app/telemetry/exporter.py
**Non-goals:** No behavior changes.
**Acceptance:**
- `acwr_recompute {policy_acwr, admin_enabled} -> {effective}` breadcrumb is logged.

