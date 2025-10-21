# BUILD 05 — Audio/ASR, LLM, TTS Foundations

**Alignment guard (do not omit):**
- Align with SSOT in `/docs`. Touch only listed files. ≤ 500 LOC/file; ≤ 3 files/task.
- Preserve `chat.v2`; adapters are pluggable and stubbed where noted.

---

### B5-A: ASR Adapter (Stub)
**Files:** `app/voice_v2/asr.py` (new)  
**Non-goals:** Vendor decode  
**Acceptance:**  
- Accepts PCM/Opus boundaries; raises partial/final callbacks (stub); telemetry `EVT_ASR_PARTIAL/EVT_ASR_FINAL`.

---

### B5-B: LLM Adapter (Stub)
**Files:** `app/voice_v2/llm.py` (new)  
**Non-goals:** Provider calls  
**Acceptance:**  
- Returns canned reply + timing; telemetry `EVT_NLG` (final).

---

### B5-C: TTS Adapter (Stub)
**Files:** `app/voice_v2/tts.py` (new)  
**Non-goals:** Synthesis  
**Acceptance:**  
- Accepts text; returns envelope with fake duration/size; telemetry `EVT_TTS_START/EVT_TTS_END`.

---

### B5-D: Cancellation Hooks (Barge-in)
**Files:** `app/voice_v2/engine.py` (update), `app/voice_v2/tts.py` (update)  
**Non-goals:** Client UI; vendor cancellation  
**Acceptance:**  
- `cancel_current_tts()` implemented; barge-in during `Responding` triggers mask teardown + cancel hook; emits `EVT_BARGE_IN` + `EVT_TTS_END` (reason=`canceled`).

---

### B5-E: Audio Envelope & Jitter Buffer
**Files:** `docs/10_CONTRACT_WS.md` (update), `app/ws/adapter.py` (update), `app/voice_v2/asr.py` (update)  
**Non-goals:** Advanced PLC  
**Acceptance:**  
- Header frame documents sample rate/channels/codec; per-sid seq reordering window; gaps flagged as `EVT_AUDIO_GAP`; size/sequence checks enforced.

---

### B5-F: Provider Interfaces & Circuit Breakers
**Files:** `app/voice_v2/asr_base.py` (new), `app/voice_v2/tts_base.py` (new), `app/voice_v2/llm_base.py` (new), `docs/30_ADR.md` (update)  
**Non-goals:** Concrete vendors  
**Acceptance:**  
- Abstract base classes with timeouts/retries; provider registry; breaker emits `EVT_PROVIDER_OPEN/EVT_PROVIDER_TRIP/EVT_PROVIDER_CLOSE`; tests simulate fail/open.

> “Return only diffs for the files listed above. Do not modify or create any other files.”
