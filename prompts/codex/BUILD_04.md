# BUILD 04 — Gate & Barge (auto only)

**Alignment guard (do not omit):**
- This build MUST align with the SSOT docs in `/docs` and all previous builds.
- Do not touch files outside the listed scope.
- Do not rename routes, env vars, or policy keys.
- Every new/changed file ≤ 500 lines. Keep changes small and isolated.
- Preserve the `chat.v2` contract and include the telemetry block in policy frames.

## Tasks
### B4-A: GateController
**Files:** app/voice_v2/gate_controller.py
**Non-goals:** No PTT; auto-only.
**Acceptance:**
- Gate on/off events include reasons (tts|post_hold|policy).
- Respects telemetry categories and levels.

### B4-B: Auto barge-in decision
**Files:** app/voice_v2/engine.py, app/telemetry/exporter.py
**Non-goals:** No client changes.
**Acceptance:**
- If `barge_in_enabled=false` during TTS, ignore speech; if true, allow interrupt and emit `EVT_BARGE_IN {source}`.

