# Architecture Build Order (v2)

> Build sizes are **Codex-friendly** (≤3 files / task, ≤300–500 lines).  
> Every task has explicit **Non-goals** and **Acceptance**.  
> **v2-only**: Do **not** add any legacy v1 routes, flags, or shims.

## Build 4 — Gate & Barge (auto only) — **This build**
- **Outbound WS Bridge**: Adapter subscribes to `EVT_WS_JSON_SEND`, forwards **allowed** payloads for its `sid` only, via a bounded outbox queue and background sender task.
  - **Allow‑list** now: `{"policy.interaction", "info", "tts.start", "tts.end", "asr.ready", "asr.partial", "asr.final", "error"}`
  - **Queue**: bounded (e.g., 256); drop‐newest on overflow; emit `EVT_WS_OUTBOX_DROP`.
  - **Lifecycle**: Subscribe **before** engine publishes initial snapshot; unsubscribe + cancel sender task on **every** close path.
  - **Ping**: Server emits periodic WS `ping` as keepalive.
  - **Acceptance:** Client receives **exactly one** `policy.interaction` on connect; multi‑session isolation (no cross‑sid leakage); drop telemetry increments under stress.

- **Mask Breadcrumbs**: Emit `EVT_TTS_MASK {phase:"engaged"|"cleared"}` on TTS start/end (in addition to existing TTS + gate events).
  - **Acceptance:** Order: `engaged` before `cleared`; while engaged, `EVT_MIC_GATE.effective=true` with reason `tts_active`.

- **Telemetry Parity**: All events normalized to include `"schema_version":"1"` (centralized in the bus).
  - **Acceptance:** Mixed events from adapter/engine observed with `schema_version:"1"`.

- **Docs**: `doc/10_CONTRACT_WS.md` updated with canonical error taxonomy and v2‑only semantics; `doc/05_REPO_STRUCTURE.prompt.md` synced with actual file names.

## Notes for later builds
- Build 5: `req_id` minted at `EVT_TURN_BEGIN` and propagated (ASR→NLU→NLG).
- Build 7: Consider adding `seq` to server→client frames for resume.


### Build 5 — Clarifications & Acceptance Expansions

- **B5-G — Turn & Request Correlation**
  - **Files:** `app/voice_v2/engine.py` (upd)
  - **Acceptance:** Emit `EVT_TURN_BEGIN {turn_id, req_id}` at turn start; carry the same `req_id` across `EVT_ASR_* → EVT_NLU → EVT_POLICY_DECISION → EVT_NLG`; emit exactly one `EVT_TURN_END {turn_id}` per turn.

- **B5-H — ASR Readiness Gate**
  - **Files:** `app/ws/adapter.py` (upd)
  - **Acceptance:** Reject inbound binary audio **before** `asr.ready` with `{"type":"error","code":"audio_not_expected"}` and close **1003**; accept after `asr.ready`.

- **B5-A — ASR Adapter (expand acceptance)**
  - Include **provider keepalive**: start `DG_KEEPALIVE_INTERVAL_S` (default **5.0s**) on connect; stop cleanly on close.
  - Emit `EVT_ASR_READY {vendor}` on warm-up.

- **B5-E — Audio Envelope & Jitter Buffer (expand acceptance)**
  - Per-session sequence with reordering window **W=8** (configurable).
  - Frames older than window are dropped; gaps emit `EVT_AUDIO_GAP {from_seq,to_seq}`.
  - Oversize binary ⇒ `frame_too_large` then WS close **1009**.
  - Malformed header or mismatch ⇒ `schema_invalid` then WS close **1003**.

- **B5-F — Circuit Breaker thresholds (minimal)**
  - Trip after 3 consecutive provider errors **or** 2 timeouts within 30s; half‑open after 15s; reset on success.
  - Tests simulate failure→`EVT_PROVIDER_TRIP` and recovery→`EVT_PROVIDER_CLOSE` (or OPEN).

- **B5-D — Cancellation Hooks (expand acceptance)**
  - After barge-in cancel, assert `EVT_TTS_MASK {phase:"cleared"}` and the latest `EVT_MIC_GATE` removes `tts_active`.

- **Test Matrix (edge cases)**
  - Duplicate `final` notices from ASR are suppressed (one `EVT_ASR_FINAL`/turn).
  - Vendor swap mid-session is not supported (reject or defer).
  - Header mismatch (declared PCM 16k vs actual 48k) ⇒ `schema_invalid` + close 1003.
  - Partial latency budget exceed triggers circuit breaker and `EVT_PROVIDER_TRIP`.
  - Redaction: provider opaque IDs appear only in `EVT_VENDOR_DEBUG`, never in client-visible frames.

- **B4-H:** **Outbound allow‑list extension (Chat frames)**  
  **Files:** `app/ws/adapter.py` (update), `tests/test_adapter_outbound_bridge.py` (update)  
  **Non‑goals:** No UI work; no changes to error taxonomy.  
  **Change:** Extend the outbound bridge **allow‑list** to include chat frames so the client receives chat alongside policy/tts/asr:  
  `chat.message`, `chat.history`  
  **Acceptance:**  
   • `chat.message` frames for the active `sid` are forwarded to the client; other sids are not.  
   • `chat.history` is forwarded when published (e.g., on connect/resume).  
   • Non‑allow‑listed types (e.g., `vendor.debug`) are still dropped.  
   • Existing allow‑listed types (`policy.interaction`, `tts.*`, `asr.*`, `error`) continue to forward unchanged.  
   • Tests updated to assert forwarding of `chat.message` and dropping of non‑allow‑listed frames.

