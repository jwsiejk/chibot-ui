# BUILD 07 — Client v2 Runtime (WS, Recorder, Playback, Policy Inspector)

**Alignment guard (do not omit):**
- Align with SSOT in `/docs`. Touch only listed files. ≤ 500 LOC/file; ≤ 3 files/task.
- Preserve `chat.v2`; client honors policy.telemetry guardrails.

---

### C7-A — WS Layer + PolicyBus
**Files:** `static/js/v2/ws.js` (new), `static/js/v2/policy_bus.js` (new)  
**Acceptance:** Connects with subprotocol `chat.v2`; handles `policy:applied`; exposes subscribe(); sticky until replaced.

---

### C7-B — Waveform + State Badges
**Files:** `static/js/v2/waveform.js` (new), `static/js/v2/state_badges.js` (new)  
**Acceptance:** Renders live waveform; badges reflect `Ready/Listening/Thinking/Responding` and TTS mask truth.

---

### C7-C — Playback Truth + Auto Barge Hooks
**File:** `static/js/v2/playback.js` (new)  
**Acceptance:** `onplay/onended` updates truth and posts telemetry; toggles auto barge behavior per policy snapshot.

---

### C7-D — Recorder + Sender
**File:** `static/js/v2/recorder.js` (new)  
**Acceptance:** Sends format header + timed chunks; handles partial/final frames; mic start/stop telemetry.

---

### C7-E — Recorder Contract & StartOnce
**Files:** `static/js/v2/recorder.js` (upd), `docs/10_CONTRACT_WS.md` (examples)  
**Acceptance:** Deterministic cadence + header fields; idempotent bootstrap event; retries safe; telemetry for bootstrap order.

---

### C7-F — Reconnect UX + Resume
**File:** `static/js/v2/ws.js` (upd)  
**Acceptance:** Exponential backoff; resume token support if server offers; duplicate suppression window.

---

### B7-E — Policy Inspector (Read-only)
**File:** `static/js/admin/policy_inspector.js` (new)  
**Acceptance:** Shows current applied snapshot + diffs; indicates default vs override sources.
