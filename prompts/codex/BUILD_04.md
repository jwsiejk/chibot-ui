# BUILD 04 — Gate Model, TTS Mask, and Turn State Machine

**Alignment guard (do not omit):**
- Align with SSOT in `/docs`. Touch only listed files. ≤ 500 LOC/file; ≤ 3 files/task.
- Preserve `chat.v2`; all gating reflected by telemetry.

---

### B4-A: Mic Gate Reason Model
**Files:** `app/voice_v2/gate.py` (new), `app/voice_v2/engine.py` (update)  
**Non-goals:** ASR  
**Acceptance:**  
- Reasons: `tts_active`, `manual_gate`, `system_hold`; effective computed; telemetry includes reasons[] + effective boolean.

---

### B4-B: TTS Mask Lifecycle Hooks
**Files:** `app/voice_v2/engine.py` (update)  
**Non-goals:** Playback implementation  
**Acceptance:**  
- Server “assistant speaking” sets `tts_active`; cleared on end; emits `EVT_MIC_GATE` breadcrumbs.

---

### B4-C: Engine Turn State Machine
**Files:** `app/voice_v2/engine.py` (update), `docs/15_NLU_NLG.md` (update)  
**Non-goals:** Vendor integration  
**Acceptance:**  
- States: `Ready → Listening → Thinking → Responding → Ready`; emits `EVT_TURN_BEGIN/EVT_TURN_END` with `turn_id`.  
- Idle/timeout transitions emit `EVT_TIMEOUT` with reason (`asr_stall`, `llm_timeout`, `tts_start_delay`).  
- Barge-in during `Responding` sets cancellation flag for TTS (hook added, actual cancellation in Build 05).

> “Return only diffs for the files listed above. Do not modify or create any other files.”
