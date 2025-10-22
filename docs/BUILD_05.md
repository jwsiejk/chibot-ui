# BUILD 05 — Audio/ASR, LLM, TTS Foundations

**Alignment guard (do not omit):**
- Align with SSOT in `/docs`. Touch only listed files. ≤ 500 LOC/file; ≤ 3 files/task.
- Preserve `chat.v2`; adapters are pluggable.

---

### B5-A — ASR Adapter (Stub)
**File:** `app/voice_v2/asr.py` (new)  
**Acceptance:** Accepts PCM/Opus boundaries; raises partial/final callbacks (stub); telemetry `EVT_ASR_PARTIAL/FINAL`.

---

### B5-B — LLM Adapter (Stub)
**File:** `app/voice_v2/llm.py` (new)  
**Acceptance:** Returns canned reply + timing; telemetry `EVT_NLG` (final).

---

### B5-C — TTS Adapter (Stub)
**File:** `app/voice_v2/tts.py` (new)  
**Acceptance:** Accepts text; returns envelope with fake duration/size; telemetry `EVT_TTS_START/END`.

---

### B5-D — Cancellation Hooks (Barge-in)
**Files:** `app/voice_v2/engine.py` (upd), `app/voice_v2/tts.py` (upd)  
**Acceptance:** `cancel_current_tts()`; barge-in during Responding triggers mask teardown + cancel; emits `EVT_BARGE_IN` + `EVT_TTS_END` (reason=`canceled`).

---

### B5-E — Audio Envelope & Jitter Buffer
**Files:** `docs/10_CONTRACT_WS.md` (examples), `app/ws/adapter.py` (upd), `app/voice_v2/asr.py` (upd)  
**Acceptance:** Header validated; per-sid seq reordering window; gaps flagged as `EVT_AUDIO_GAP`; size/sequence checks enforced.

---

### B5-F — Provider Interfaces & Circuit Breakers
**Files:** `app/voice_v2/asr_base.py` (new), `app/voice_v2/tts_base.py` (new), `app/voice_v2/llm_base.py` (new), `docs/30_ADR.md` (update)  
**Acceptance:** Abstract base classes with timeouts/retries; provider registry; breaker emits `EVT_PROVIDER_OPEN/TRIP/CLOSE`; tests simulate fail/open.


**Smoke acceptance (minimal trace):**
- On startup, log `EVT_ASR_READY {vendor}`.
- Simulate a user turn: partials → `EVT_ASR_FINAL` (exactly once) → `EVT_NLU` (exactly once) → `EVT_POLICY_DECISION` → `EVT_NLG` (exactly once), all with the same `req_id`.
- Switch `ASR_VENDOR` to `speechmatics` and observe identical event shapes with `vendor:"speechmatics"`.

> "Return only diffs for the files listed above. Do not modify or create any other files."

---

### B5-I — Dual‑VAD Aggregator & Policy (production‑grade)
**Files:** `app/voice_v2/vad.py` (new), `app/voice_v2/engine.py` (update), `tests/test_dual_vad_arbiter.py` (new)  
**Policy:** Add server‑side `policy.vad` with: `mode("or"|"and"|"priority")`, `priority("asr"|"auto")`, `min_speech_ms`, `energy_threshold_dbfs`, `hold_ms`, `echo_suppression_ms`, `barge_cooldown_ms`.  
**Behavior:** Fuse auto‑VAD and ASR evidence into one decision; echo‑suppression, hysteresis, cooldown; one‑grant‑per‑TTS; per‑session isolation.  
**Adaptation:** Per‑session noise‑floor tracking → SNR threshold; environment classification (quiet/normal/noisy) adjusts parameters within safe bounds; bounded self‑tuning with telemetry audit.  
**Telemetry:** Diagnostic `EVT_VAD` and `EVT_VAD_DECISION`; outcome continues via `EVT_BARGE_IN`.  
**Acceptance:** OR/AND/priority tests; echo‑suppress; hysteresis & cooldown; session isolation; adaptation behaves under sustained noise vs quiet.
