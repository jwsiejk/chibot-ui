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

### B5-G — Turn & Request Correlation
**Files:** `app/voice_v2/engine.py` (upd)  
**Acceptance:** Emit `EVT_TURN_BEGIN {turn_id, req_id}` at turn start; carry the same `req_id` across `EVT_ASR_* → EVT_NLU → EVT_POLICY_DECISION → EVT_NLG`; emit exactly one `EVT_TURN_END {turn_id}` per turn.

---

### B5-H — ASR Readiness Gate
**Files:** `app/ws/adapter.py` (upd)  
**Acceptance:** Reject inbound binary audio **before** `asr.ready` with `{"type":"error","code":"audio_not_expected"}` and close **1003**; accept after `asr.ready`.

---

### Test Matrix (edge cases)
- Duplicate `final` notices from ASR are suppressed: exactly one `EVT_ASR_FINAL` per turn.
- Vendor swap mid-session is not supported; verify it's rejected or deferred.
- Header mismatch (declared PCM 16k vs actual 48k) ⇒ `schema_invalid` + close 1003.
- Partial latency budget exceeded triggers circuit breaker path and `EVT_PROVIDER_TRIP`.
- Redaction: provider opaque IDs appear only in `EVT_VENDOR_DEBUG`, never in client-visible frames.

---

### Contract updates (10_CONTRACT_WS.md)
- Add `audio.header` `seq_start` example and note ASR readiness gate.
- Document binary sequence window (W), gap telemetry (`EVT_AUDIO_GAP`), and oversize/malformed close codes.

> **Note for B5-A:** Start a provider keepalive task with `DG_KEEPALIVE_INTERVAL_S` (default 5.0s) on connect; stop it cleanly on close; emit `EVT_ASR_READY {vendor}` on warm-up.


> **Note for B5-D:** After barge-in cancel, assert `EVT_TTS_MASK {phase:"cleared"}` and latest `EVT_MIC_GATE` removes `tts_active`.
