# BUILD 07 — Client v2 Runtime (WS, Recorder, Playback, Policy Inspector)

**Alignment guard (do not omit):**
- Align with SSOT in `/docs`. Touch only listed files. ≤ 500 LOC/file; ≤ 3 files/task.
- Preserve `chat.v2`; client honors policy.telemetry guardrails.

---

### C7-A: WS Layer + PolicyBus
**Files:** `static/js/v2/ws.js` (new), `static/js/v2/policy_bus.js` (new)  
**Non-goals:** Visual chrome  
**Acceptance:**  
- Connects using subprotocol `chat.v2`; handles `policy:applied`; exposes subscribe(); sticky until replaced.

---

### C7-B: Waveform + State Badges
**Files:** `static/js/v2/waveform.js` (new), `static/js/v2/state_badges.js` (new)  
**Non-goals:** Design polish  
**Acceptance:**  
- Renders live waveform; badges reflect `Ready/Listening/Thinking/Responding` and TTS mask truth.

---

### C7-C: Playback Truth + Auto Barge Hooks
**Files:** `static/js/v2/playback.js` (new)  
**Non-goals:** TTS engine  
**Acceptance:**  
- `onplay/onended` updates truth and posts telemetry; toggles auto barge behavior strictly per policy snapshot.

---

### C7-D: Recorder + Sender
**Files:** `static/js/v2/recorder.js` (new)  
**Non-goals:** Vendor ASR client  
**Acceptance:**  
- Sends format header + timed chunks; handles partial/final frames; mic start/stop telemetry.

---

### C7-E: Recorder Contract & StartOnce
**Files:** `static/js/v2/recorder.js` (update), `docs/10_CONTRACT_WS.md` (update)  
**Non-goals:** UI controls beyond basics  
**Acceptance:**  
- Deterministic cadence + header fields; “start-once” idempotent bootstrap event; retries safe; telemetry for bootstrap order.

---

### C7-F: Reconnect UX + Resume
**Files:** `static/js/v2/ws.js` (update)  
**Non-goals:** Offline cache  
**Acceptance:**  
- Exponential backoff; resume token support if server offers; duplicate suppression window; visual indicator of reconnecting.

---

### B7-E: Policy Inspector (Read-only)
**Files:** `static/js/admin/policy_inspector.js` (new)  
**Non-goals:** Editing  
**Acceptance:**  
- Shows current applied snapshot + diffs; indicates default vs override sources; subscribes to policy updates.

> “Return only diffs for the files listed above. Do not modify or create any other files.”
