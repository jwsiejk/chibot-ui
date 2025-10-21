# BUILD 03 — WebSocket Framing, Routing, and Versioning

**Alignment guard (do not omit):**
- Align with SSOT in `/docs`. Touch only listed files. ≤ 500 LOC/file; ≤ 3 files/task.
- Preserve `chat.v2` contract; extend spec with version negotiation and error taxonomy.

---

### B3-A: JSON Frame Contract (Spec Parity)
**Files:** `docs/10_CONTRACT_WS.md` (update), `app/ws/adapter.py` (update)  
**Non-goals:** ASR/TTS logic  
**Acceptance:**  
- All documented frame types/required fields enforced; unknown types → error frame.  
- Clean Ping/Pong; strict separation of text vs binary with telemetry taps.

---

### B3-B: Binary Routing Guard
**Files:** `app/ws/adapter.py` (update)  
**Non-goals:** Decoding; resampling  
**Acceptance:**  
- Binary accepted only when engine expects audio (engine flag or mode); otherwise 409 + error frame; `EVT_WS_AUDIO_RECV` includes `byte_count` and `seq`.

---

### B3-C: Version Negotiation & Error Taxonomy
**Files:** `docs/10_CONTRACT_WS.md` (update), `app/ws/adapter.py` (update), `app/voice_v2/engine.py` (update)  
**Non-goals:** Support for legacy `v1` behavior  
**Acceptance:**  
- Server advertises `supported_subprotocols: ["chat.v2"]` and optional `min_version`.  
- If mismatched, HTTP 426 with structured JSON: `{supported:["chat.v2"], reason:"..."}`.  
- Standard error frame schema `{type:"error", code, hint?, retryable?}` documented and used by adapter/engine.  
- Backpressure telemetry `EVT_BACKPRESSURE_ON/OFF` with thresholds and queue depth fields.

> “Return only diffs for the files listed above. Do not modify or create any other files.”
