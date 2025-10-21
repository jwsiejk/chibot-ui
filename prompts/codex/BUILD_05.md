# BUILD 05 — ASR Manager + NLU/NLG seams

**Alignment guard (do not omit):**
- This build MUST align with the SSOT docs in `/docs` and all previous builds.
- Do not touch files outside the listed scope.
- Do not rename routes, env vars, or policy keys.
- Every new/changed file ≤ 500 lines. Keep changes small and isolated.
- Preserve the `chat.v2` contract and include the telemetry block in policy frames.

## Tasks
### B5-A: ASRManager (Deepgram)
**Files:** app/voice_v2/asr_manager.py
**Non-goals:** No Speechmatics yet.
**Acceptance:**
- `warm_up()` emits `EVT_ASR_READY`; partial/final events carry `req_id`.

### B5-B: Speechmatics adapter + selection
**Files:** app/voice_v2/asr_manager.py
**Non-goals:** Do not break Deepgram path.
**Acceptance:**
- Admin/env can switch vendor; event shapes remain identical.

### B5-C: NLU hook
**Files:** app/voice_v2/engine.py, app/voice_v2/nlu.py, app/telemetry/exporter.py
**Non-goals:** No heavy ML; placeholder is ok.
**Acceptance:**
- After `asr.final`, exactly one NLU object per turn is logged to `nlu/turns.ndjson.gz`.

### B5-D: Dialog policy + NLG hook
**Files:** app/voice_v2/dialog_policy.py, app/voice_v2/nlg.py, app/voice_v2/engine.py, app/telemetry/exporter.py
**Non-goals:** No tools/workflows yet.
**Acceptance:**
- Decision logged with reason; one NLG object per turn to `nlg/turns.ndjson.gz`.

