# BUILD 04 — Gate Model, TTS Mask, and Turn State Machine

**Alignment guard (do not omit):**
- Align with SSOT in `/docs`. Touch only listed files. ≤ 500 LOC/file; ≤ 3 files/task.
- Preserve `chat.v2`; all gating reflected by telemetry.

---

### B4-A — Mic Gate Reason Model
**Files:** `app/voice_v2/gate.py` (new), `app/voice_v2/engine.py` (upd)  
**Acceptance:** Reasons: `tts_active`, `manual_gate`, `system_hold`; effective computed; emits `EVT_MIC_GATE` with reasons[] + effective.

---

### B4-B — TTS Mask Lifecycle Hooks
**File:** `app/voice_v2/engine.py` (upd)  
**Acceptance:** Assistant speaking sets `tts_active`; cleared on end; breadcrumbs logged.

---

### B4-C — Engine Turn State Machine
**Files:** `app/voice_v2/engine.py` (upd), `docs/15_NLU_NLG.md` (update notes)  
**Acceptance:** States: `Ready → Listening → Thinking → Responding → Ready`; emits `EVT_TURN_BEGIN/END` and `EVT_TIMEOUT` (reasons).
