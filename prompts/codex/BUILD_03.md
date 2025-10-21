# BUILD 03 — TTS Tracker

**Alignment guard (do not omit):**
- This build MUST align with the SSOT docs in `/docs` and all previous builds.
- Do not touch files outside the listed scope.
- Do not rename routes, env vars, or policy keys.
- Every new/changed file ≤ 500 lines. Keep changes small and isolated.
- Preserve the `chat.v2` contract and include the telemetry block in policy frames.

## Tasks
### B3-A: Tracker module
**Files:** app/voice_v2/tts_tracker.py
**Non-goals:** No player integration yet.
**Acceptance:**
- `start(utt_id, now_ms)` and `end(utt_id, now_ms)` publish EVT_TTS_START/END.
- `release_at_ms = end + post_hold_ms`.

### B3-B: Engine integration
**Files:** app/voice_v2/engine.py, app/telemetry/exporter.py
**Non-goals:** No ASR yet.
**Acceptance:**
- `tts.start … (seconds) … tts.end` recorded; after post-hold, policy sets idle with `acwr:true`.

