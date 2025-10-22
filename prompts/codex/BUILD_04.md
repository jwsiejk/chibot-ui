# BUILD 04 — Gate Model, TTS Mask, and Turn State Machine

**Alignment guard (do not omit):**
- Align with SSOT in `/docs` (`00_CONTEXT.md`, `10_CONTRACT_WS.md`, `15_NLU_NLG.md`, `20_ARCH_BUILD_ORDER.md`, `30_ADR.md`). Touch only listed files. ≤ 500 LOC/file; ≤ 3 files/task.
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



---

### B4-D — Auto Barge Event Schema & Decision
**File:** `app/voice_v2/engine.py` (upd)
**Non-goals:** No manual PTT UI or settings; no vendor specifics.
**Acceptance:**
- Emit `EVT_BARGE_IN { source:"auto_vad"|"asr_evidence", granted:bool, reason?:string, ts_ms }` on each attempt while `AssistantSpeaking`.
- `granted` respects current policy `barge_in_enabled` and engine state; denied events must still be logged.
- Correlate a granted barge with transition to `ConfirmingBarge` → `Listening` (see B4-C).

**Smoke acceptance (minimal trace):**
- Start TTS → log `EVT_TTS_MASK {phase:"engaged"}` and `EVT_MIC_GATE {effective:true, reasons:["tts_active"]}`.
- State moves `Idle → AssistantSpeaking` on TTS start; returns on TTS end.
- During `AssistantSpeaking`, trigger one auto-barge attempt → log `EVT_BARGE_IN {source, granted}` and a matching `EVT_ENGINE_STATE` transition (`ConfirmingBarge → Listening` if granted, or back to `AssistantSpeaking` if denied).
- End TTS → log `EVT_TTS_MASK {phase:"cleared"}` and `EVT_MIC_GATE {effective:false, reasons:[]}`.

> "Return only diffs for the files listed above. Do not modify or create any other files."