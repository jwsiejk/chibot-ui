# BUILD 06 — Telemetry Exporter (full)

**Alignment guard (do not omit):**
- This build MUST align with the SSOT docs in `/docs` and all previous builds.
- Do not touch files outside the listed scope.
- Do not rename routes, env vars, or policy keys.
- Every new/changed file ≤ 500 lines. Keep changes small and isolated.
- Preserve the `chat.v2` contract and include the telemetry block in policy frames.

## Tasks
### B6-A: Bundle structure & redaction
**Files:** app/telemetry/exporter.py
**Non-goals:** Do not change bus or engine public APIs.
**Acceptance:**
- Bundle contains `manifest.json`, `server/server.log.gz`, `ws/frames.ndjson.gz`, `events/flow.ndjson.gz`; secrets masked and PII redaction honored.

### B6-B: Levels/categories/sampling
**Files:** app/telemetry/exporter.py, app/telemetry/bus.py
**Non-goals:** No client changes.
**Acceptance:**
- Toggling telemetry in policy changes logging immediately.
- `level=trace` adds granular taps; `sampling.percent` is respected.

### B6-C: Provider debug channels
**Files:** app/telemetry/exporter.py
**Non-goals:** No sensitive values in logs.
**Acceptance:**
- ASR/TTS/LLM request IDs + timings captured (no secrets).

