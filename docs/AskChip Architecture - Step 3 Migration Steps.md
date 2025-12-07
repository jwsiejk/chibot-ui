# AskChip Architecture – Step 3: Migration Plan (Google Flow V3)

> **Scope:** This document describes *how to get from* the current implementation (Step 1) *to* the target “Google Flow V3” design (Step 2).  
> It is organized by subsystem (server, client) and by rollout phase, with explicit guardrails and test plans.

---

## 1. Objectives and Constraints

### 1.1 Objectives

1. Implement the **Google Flow V3** speech pipeline as defined in Step 2:
   - Vendor ASR streams are opened only when we have audio to send.
   - VAD sees continuous PCM; ASR sees pre-roll + live audio per user turn.
   - Greet → ConversationReady → UserTurn is stable and predictable.
   - Nudges and barge-in behave as specified, without destabilizing the system.

2. Deliver the migration in **small, verifiable phases**:
   - Each phase should be deployable and testable in isolation.
   - Feature gating must allow easy rollback to current behavior.

3. Maintain **backwards compatibility** for:
   - WS protocol (paths, top-level message types),
   - Existing admin/debug tools,
   - Existing login/profile flows.

### 1.2 Non-Goals

- No schema migrations or changes to long-term data storage beyond optional new flags/config.
- No change to product-level auth, admin UI, or user-visible branding beyond minor additions for observability.

### 1.3 Cross-Cutting Constraints

- All new work must be guarded behind a **feature flag** (e.g., `GOOGLE_V3_ENABLED`).
- Existing behavior (Step 1) must remain intact when the flag is disabled.
- Logging for the new flow must be **low-noise but sufficient** to debug gate/ASR issues.

---

## 2. Rollout Strategy

We will deliver Google Flow V3 in **four phases**:

1. **Phase 0 – Plumbing & Feature Flags**
   - Introduce the configuration scaffolding and no-op hooks on both client and server.
   - No behavior change yet.

2. **Phase 1 – Client Audio Refactor (Ring Buffer + Gating Split)**
   - Implement the client-side ring buffer and three-layer gating model.
   - Still open ASR as today (Step 1) but begin collecting diagnostics as if V3 were active.

3. **Phase 2 – Server ASR Lifecycle Changes**
   - Move ASR open/close to the V3 model (open on speech/pre-roll, close per turn).
   - Keep V3 under feature flag; compare behavior vs legacy.

4. **Phase 3 – Nudge & Barge-In Refinement + Default On**
   - Wire up full V3 nudge and barge-in behavior.
   - Harden observability and make V3 the default path once acceptance criteria are met.

Each phase has its own **acceptance checklist** and can be rolled back by disabling `GOOGLE_V3_ENABLED`.

---

## 3. New Configuration & Feature Flags

Add new config entries (names illustrative; adjust to match your existing `config.py` patterns):

### 3.1 Server Config (config.py)

- `GOOGLE_V3_ENABLED: bool = False`
- `GOOGLE_V3_PRE_SPEECH_BUFFER_MS: int = 700`
- `GOOGLE_V3_SPEECH_START_MIN_FRAMES: int = 5`
- `GOOGLE_V3_SILENCE_END_MS: int = 800`
- `GOOGLE_V3_GREET_SILENCE_NUDGE_MS: int = 7000`
- `GOOGLE_V3_BARGE_IN_ENABLED: bool = True`

Add these to the policy/env loader in `app/config.py` and propagate into:

- `app/ws/adapter.py` (ASR lifecycle),
- `app/voice_v2/engine.py` (policy decisions, greet vs conversation),
- possibly a dedicated `google_v3.py` config helper for clarity.

### 3.2 Client Config

Expose a small, structured config object in JS, derived from server policy (e.g., included in the initial `policy.interaction` message):

- `googleV3Enabled`
- `preSpeechBufferMs`
- `speechStartMinFrames`
- `silenceEndMs`
- `greetSilenceNudgeMs`
- `bargeInEnabled`

This keeps server and client in sync for thresholds and behaviors.

---

## 4. Phase 0 – Plumbing & Feature Flags

### 4.1 Server Changes

**Files:**

- `app/config.py`
- `app/ws/adapter.py`
- `app/voice_v2/engine.py`

**Tasks:**

1. Add `GOOGLE_V3_ENABLED` and the other knobs to `config.py`.
2. In `app/ws/adapter.py`, add helper methods / placeholders:
   - `is_google_v3_enabled(session)`
   - `google_v3_log(...)` (simple wrapper around telemetry/logging with a clear label).
3. In `EngineV2`, add a field in session state to record whether the current session is running V3 or legacy:
   - `session.google_v3_enabled: bool`.

**Behavior:** No functional change yet; only plumbing and logging macros.

**Acceptance Criteria:**

- App boots and behaves identically with `GOOGLE_V3_ENABLED=false`.
- Logs confirm the new config keys can be read and included in policy bundles.

---

## 5. Phase 1 – Client Audio Refactor (Ring Buffer + Three-Layer Gating)

This phase refactors the client-side audio pipeline while still using the **current** server ASR lifecycle. The goal is to:

- Introduce the ring buffer,
- Split gating into **hard gate** vs **soft VAD gate** vs **policy layer**,
- Start logging V3-style decisions, but **not** change when the server opens ASR yet.

### 5.1 Client Modules

**Files:**

- `app/static/js/audio/ws_audio_runtime.js`
- `app/static/js/app.js`
- `app/static/js/voice/phase_controller.js`
- `app/static/js/ws/connection.js` (for wsPhase integration)

### 5.2 Implementation Tasks

1. **Ring Buffer**

   - Add an in-memory circular buffer in `ws_audio_runtime.js` that always stores the last `preSpeechBufferMs` worth of PCM frames at 16k.
   - Ensure:
     - The buffer is filled irrespective of gating (hard gate can be closed, buffer still receives PCM from the audio graph).
     - It is cheap to drain into the WS sender when a turn begins.

2. **Three-Layer Gating**

   Refactor `ws_audio_runtime.js` so that:

   - **Hard gate** depends on:
     - `wsPhase` ∈ {connected, ready},
     - `AppState.phase` ∈ {conversation_ready, user_turn},
     - `serverHold` / `systemHold` / fatal flags.
   - **Soft gate (VAD silencer)**:
     - Before `speechSeenThisTurn`:
       - Accepts all frames into the ring buffer, but drops most from WS unless above speech threshold.
     - After `speechSeenThisTurn`:
       - Sends frames for the active turn until silence is detected.
   - **Policy layer**:
     - Exposes hooks for:
       - “no speech after greet” timers,
       - barge-in detection,
       - UX nudges.
     - For now, only logs events (no UI changes yet).

3. **Compatibility Mode**

   - When `googleV3Enabled === false`:
     - Continue to drive ASR open behavior as currently implemented (Step 1).
     - Still maintain ring buffer and additional logging, but do **not** send pre-roll or change control messages yet.

4. **Logging**

   Add low-rate logs in `ws_audio_runtime.js`:

   - When `speechSeenThisTurn` flips from false → true.
   - When the hard gate denies sending while VAD reports speech.
   - When ring buffer is drained (in future phases).

### 5.3 Acceptance Criteria

- With `GOOGLE_V3_ENABLED=false`, behavior matches Step 1 (no regression).
- Logs clearly show:
  - Continuous ring buffer operation.
  - VAD events (speech start/end).
  - Hard gate decisions (allowed vs denied).

---

## 6. Phase 2 – Server ASR Lifecycle (Open on Speech + Pre-Roll)

This is the core server-side change: implementing the V3 ASR lifecycle.

### 6.1 Protocol Additions

Decide on the exact control messages; one plausible pattern:

- `client.turn_start` (from client to server)
  - Fields: `turn_id`, `pre_roll_ms`, optional VAD confidence.
- `client.turn_stop`
- `client.barge_request`
- `server.turn_started`, `server.turn_stopped` (optional, for explicit ACKs).

These can be realized as:

- Dedicated message types, or
- Namespaced within an existing message envelope (`type: "control", label: "turn_start"`, etc.).

The important part is that the adapter can distinguish:

- Legacy behavior vs V3 behavior,
- Which PCM frames belong to which turn.

### 6.2 Adapter Changes (`app/ws/adapter.py`)

**Tasks:**

1. **Turn State Tracking**

   - Add per-session fields:
     - `current_turn_id`
     - `current_turn_open` (bool)
     - `google_v3_enabled` (copied from config/policy).
   - On `client.turn_start`:
     - Validate state (no active turn).
     - Record `turn_id` and mark `current_turn_open=true`.

2. **ASR Open on First PCM (V3)**

   For sessions with `google_v3_enabled=true`:

   - When the first PCM frames arrive for a given `turn_id`:
     - Check:
       - no vendor stream currently open,
       - session is in a state that allows LISTENING,
       - policy allows capture (no system hold, TTS gating).
     - Open a Google streaming session:
       - Use `pre_roll_ms` to decide how many buffered frames to send first.
       - Immediately send pre-roll + live frames.
     - Mark:
       - `asr_stream_open = True`,
       - Engine state = LISTENING.

3. **ASR Close on Turn Stop / Final**

   - On `client.turn_stop`:
     - Stop accepting new PCM frames for that turn.
     - Close the Google stream once buffered frames are flushed.
   - On vendor final result (ASR final):
     - Close Google stream if still open.
     - Advance EngineV2 state to THINKING → RESPONDING.

4. **No-Op for Legacy**

   - For sessions with `google_v3_enabled=false`:
     - Maintain the current Step 1 behavior (open after greet, keepalive, etc.).
     - Do not rely on `turn_start` / `turn_stop`.

5. **Safety Nets**

   - Replace or tune `no_audio_timeout` to be specific to V3:
     - Only active when a V3 turn is marked open and we have expected PCM but none arrives.
   - Log `google_v3.asr_no_audio_safety_net_fired` when this safety net closes a stream.

### 6.3 EngineV2 Integration (`app/voice_v2/engine.py`)

**Tasks:**

1. Introduce explicit concept of **“Conversation Ready”** (from Step 2):
   - Ready to open ASR but without an active vendor stream.
   - Align this with EngineV2’s READY state and greet completion.

2. Ensure that EngineV2:

   - Does **not** implicitly open ASR at greet end when `google_v3_enabled=true`.
   - Waits for explicit V3 turn events from the adapter.

3. Accept and act upon:

   - Turn start / stop signals from adapter:
     - Turn start → move from READY to LISTENING.
     - Turn stop or vendor final → LISTENING → THINKING → RESPONDING.

### 6.4 Acceptance Criteria

- With `GOOGLE_V3_ENABLED=true` for a test user / environment:
  - ASR streams are opened only after `client.turn_start` + first PCM frames.
  - No vendor “Audio Timeout Error” when the user does not speak after greet.
  - Pre-roll + live frames are confirmed in logs for each turn.
- With `GOOGLE_V3_ENABLED=false`:
  - Behavior remains consistent with Step 1.

---

## 7. Phase 3 – Nudges, Barge-In, and Default On

### 7.1 Nudges (Client + Server)

**Client:**

- Implement the **greet silence nudge**:
  - Start a timer at `conversation_ready`.
  - If `speechSeenThisTurn=false` and timer exceeds `greetSilenceNudgeMs`:
    - Show one nudge in the UI.
    - Emit a `google_v3.nudge.greet_silence` log event.
- Optionally implement a long-idle nudge for extended silence later in the session.

**Server (optional):**

- Maintain a mirrored timer using session timestamps.
- Enforce a maximum nudge rate per session.

### 7.2 Barge-In

**Client:**

- While TTS is playing:
  - Monitor VAD RMS/“speech over TTS” conditions.
  - On sustained speech above threshold and `bargeInEnabled`:
    - Send `client.barge_request` with optional audio metrics.

**Server:**

- On `client.barge_request`:
  - If TTS is active and policy allows:
    - Stop or cancel current TTS.
    - Move EngineV2 state to CONFIRMING_BARGE, then READY/LISTENING for new turn.
  - Signal back to the client (e.g., `server.barge_granted` / `server.barge_denied`).

### 7.3 Default On

Once Google Flow V3 has met the **acceptance criteria** (see Step 2 §8.2) in non-production / limited production:

- Set `GOOGLE_V3_ENABLED=true` by default.
- Optionally keep a per-user override for debugging or phased rollout (`forceLegacyAudio` flag).

---

## 8. Testing & Validation

### 8.1 Unit & Integration Tests

- Add unit tests for:
  - Ring buffer behavior (wrap-around, pre-roll extraction).
  - Hard gate vs soft gate decision logic.
  - Adapter V3 ASR lifecycle (open on first PCM, close on stop/final, safety net).

- Add integration tests (can be scripted in Python or via a test harness) for:
  - Silent user after greet (no ASR stream opened, one nudge).
  - Short utterance with pre-roll (first word not clipped).
  - Long conversation with multiple turns (streams open/close cleanly).
  - Barge-in during both greet and a long TTS response.

### 8.2 Observability Checks

- Dashboards or queries over session logs to verify:
  - Ratio of `google_v3.asr_open` to actual user turns.
  - Absence of vendor “Audio Timeout” errors for V3 sessions.
  - Distribution of nudge events (no spam).

---

## 9. Risks and Mitigations

- **Risk:** Misalignment between client’s notion of `turn_start` and server’s ASR lifecycle.
  - **Mitigation:** Log turn ids in both directions and assert invariants (e.g., no PCM for unknown turn_id).

- **Risk:** Ring buffer size too small or too large.
  - **Mitigation:** Start with 700 ms, instrument timings, and tune based on logs and qualitative behavior.

- **Risk:** Gating bugs causing “speechSeenThisTurn” without bytes sent.
  - **Mitigation:** Add explicit log when VAD detects speech but `bytesSentThisTurn == 0` after N ms; treat this as a high-priority bug.

- **Risk:** Barge-in complicates state management.
  - **Mitigation:** Keep barge-in optional (`GOOGLE_V3_BARGE_IN_ENABLED`), land it last, and test thoroughly on top of a stable V3 baseline.

---

## 10. Summary

- **Step 1** describes the current AskChip implementation.
- **Step 2** defines the target Google Flow V3 behavior.
- **Step 3 (this document)** describes a staged, flag-driven path to get there:
  - Phase 0: plumbing and flags.
  - Phase 1: client audio refactor (ring buffer + gating).
  - Phase 2: server ASR lifecycle (open on speech + pre-roll).
  - Phase 3: nudges, barge-in, and default-on.

Following this plan keeps the system deployable at every step while converging on the desired V3 conversational flow.


Logging 

1. Phase 1 – Client Audio & Gating Cleanup
1.1 Client logging you should add

Goal here: prove that ring buffer + 3-layer gate are working before we touch the server.

Add rate-limited logStage / emitClientLog for:

Ring buffer health

logStage("client.google_v3.ring_buffer_status", {
  preSpeechBufferMs,
  framesStored,
  bytesStored,
});


Emit:

on init,

and then maybe every N seconds only when framesStored hits max or drains (to avoid spam).

Hard gate decisions

When you compute the hard gate snapshot in ws_audio_runtime.js:

logStage("client.google_v3.hard_gate_snapshot", {
  allowed: hardGate.allowed,
  reason: hardGate.reason,
  wsPhase,
  appPhase,
});


Emit only when:

allowed changes (false → true or true → false), or

reason changes.

That gives you a nice “state trace” of when audio is legally allowed vs blocked.

Soft gate / speech detection

When speechSeenThisTurn flips:

logStage("client.google_v3.speech_seen_this_turn", {
  speechSeenThisTurn,
  rmsAtTrigger,
  vadFramesSinceGreet,
});


Emit exactly once per user turn when the flag goes from false to true.

Send vs drop summary

In the PCM send path, instead of logging every chunk, add a periodic summary:

logStage("client.google_v3.pcm_send_summary", {
  windowMs: 2000,
  framesSent,
  framesDroppedHardGate,
  framesDroppedSoftGate, // deprecated: soft gate is telemetry-only; expected to stay 0
});


Emit every 2 seconds of active audio or on turn end.

This lets you see “VAD is firing but everything is being dropped by hard gate” instantly.

What you should see before Phase 2:

Ring buffer reports non-zero framesStored even when you’re gated (during greet).

After greet → conversation_ready, hard_gate_snapshot.allowed goes to true with a reason like ok.

When you speak:

speechSeenThisTurn fires.

pcm_send_summary.framesSent goes > 0, and drops are low or zero.

When you stay silent after greet:

speechSeenThisTurn never fires.

framesSent stays 0, but ring buffer + VAD logs show life.

1.2 Server logging in Phase 1

Phase 1 server side is mostly “read-only” relative to V3, but you can still add one small diagnostic:

When a new session starts, log:

logger.info("evt=google_v3.phase1_session", extra={
    "sid": sid,
    "note": "client gating refactor active"
})


Just so you can quickly filter sessions that are using the new client code when you inspect logs.

2. Phase 2 – Server ASR Lifecycle (Open on Speech + Pre-Roll)

This is where logging really matters, because this is the “no more audio timeout” move.

2.1 Server logging you should add

In app/ws/adapter.py, around the new turn + ASR logic, add structured logs like:

Turn start / stop

logger.info("evt=google_v3.turn_start", extra={
    "sid": sid,
    "turn_id": turn_id,
    "pre_roll_ms": pre_roll_ms,
    "ts_ms": now_ms(),
})

logger.info("evt=google_v3.turn_stop", extra={
    "sid": sid,
    "turn_id": turn_id,
    "ts_ms": now_ms(),
})


ASR open / close

At the moment you open the vendor stream:

logger.info("evt=google_v3.asr_open", extra={
    "sid": sid,
    "turn_id": turn_id,
    "sample_rate": sample_rate,
    "language": language,
})


And on close:

logger.info("evt=google_v3.asr_close", extra={
    "sid": sid,
    "turn_id": turn_id,
    "bytes_from_client": bytes_from_client,
    "bytes_to_vendor": bytes_to_vendor,
    "reason": close_reason,  # "normal_final", "turn_stop", "no_audio_safety_net", "vendor_error"
})


No-audio safety net

When your V3-style “no audio in an open turn” guard fires:

logger.warning("evt=google_v3.asr_no_audio_safety_net_fired", extra={
    "sid": sid,
    "turn_id": turn_id,
    "ms_since_turn_start": delta_ms,
})


Ideally this is rare and gets your attention immediately.

Vendor “Audio Timeout” stay

Wherever you catch the vendor “Audio Timeout Error”:

logger.error("evt=google_v3.vendor_audio_timeout", extra={
    "sid": sid,
    "turn_id": turn_id,
    "details": str(exc),
})


Post-V3, this should basically never appear in normal flows.

2.2 Client logging to correlate

Once you wire turn_start / turn_stop from the client:

Log when you send them:

logStage("client.google_v3.turn_start_sent", {
  turnId,
  preRollMs,
});

logStage("client.google_v3.turn_stop_sent", {
  turnId,
});


This lets you match client view of turns against the server logs above (event pairs per turn_id).

What you should see before Phase 3:

For a “speak after greet” session:

Sequence like:

evt=google_v3.turn_start

evt=google_v3.asr_open

evt=google_v3.asr_close reason=normal_final

evt=google_v3.turn_stop

Client side:

client.google_v3.turn_start_sent before ASR opened.

client.google_v3.turn_stop_sent near when you stop talking.

pcm_send_summary.framesSent > 0 during the turn.

For a “silent after greet” session:

No evt=google_v3.turn_start.

No evt=google_v3.asr_open.

No vendor timeout events.

3. Phase 3 – Nudges, Barge-In, and Cleanup
3.1 Client nudge logs

When you implement the greet-silence nudge:

logStage("client.google_v3.nudge.greet_silence", {
  msSinceGreetEnd,
});


Optional long-idle nudge:

logStage("client.google_v3.nudge.idle_conversation", {
  msSinceLastTurnEnd,
});


You can easily grep for client.google_v3.nudge to see if you’re over-firing.

3.2 Barge-in logs (client + server)

Client:

logStage("client.google_v3.barge_request", {
  rms,
  speechFramesOverTts,
});


Server:

On receipt:

logger.info("evt=google_v3.barge_request", extra={
    "sid": sid,
    "tts_active": tts_active,
})


On decision:

logger.info("evt=google_v3.barge_granted", extra={
    "sid": sid,
    "turn_id": new_turn_id,
})
# or
logger.info("evt=google_v3.barge_denied", extra={
    "sid": sid,
    "reason": "policy",  # or "no_tts_active"
})


What you should see:

When you intentionally barge in:

Client logs barge_request.

Server logs evt=google_v3.barge_request → ...barge_granted.

A new turn starts (you see turn_start / asr_open next).

4. How to bake this into Step 3

If you want to update the Step 3 doc so future-you doesn’t have to remember all this, you can add a short section like:

## X. Logging & Validation per Phase

### Phase 1 – Client (ws_audio_runtime.js)
- Add:
  - `client.google_v3.ring_buffer_status`
  - `client.google_v3.hard_gate_snapshot`
  - `client.google_v3.speech_seen_this_turn`
  - `client.google_v3.pcm_send_summary`
- Use these to verify:
  - Ring buffer is filling/draining correctly.
  - Hard gate opens after greet.
  - Speech detection aligns with spoken audio.
  - Frames are being sent / dropped for expected reasons.

### Phase 2 – Server (adapter + EngineV2) and Client (turn control)
- Add server logs:
  - `evt=google_v3.turn_start`, `evt=google_v3.turn_stop`
  - `evt=google_v3.asr_open`, `evt=google_v3.asr_close`
  - `evt=google_v3.asr_no_audio_safety_net_fired`
  - `evt=google_v3.vendor_audio_timeout`
- Add client logs:
  - `client.google_v3.turn_start_sent`
  - `client.google_v3.turn_stop_sent`
- Use these to verify:
  - No ASR streams open when user stays silent after greet.
  - Each utterance has a clean turn_start → asr_open → asr_close → turn_stop chain.

### Phase 3 – Nudges & Barge-In
- Add client logs:
  - `client.google_v3.nudge.greet_silence`
  - `client.google_v3.nudge.idle_conversation`
  - `client.google_v3.barge_request`
- Add server logs:
  - `evt=google_v3.barge_request`
  - `evt=google_v3.barge_granted` / `evt=google_v3.barge_denied`
- Use these to verify:
  - At most one greet-silence nudge per greet.
  - Barge-in events line up with actual user interruptions.