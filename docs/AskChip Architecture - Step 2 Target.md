# AskChip Architecture – Step 2: Target Design (Deepgram Flow V3)

> **Scope:** This document defines the *target* architecture and conversational flow for AskChip’s Deepgram-based speech pipeline (“Deepgram Flow V3”).  
> It is intentionally **forward-looking** and describes how the system *should* behave, not how it behaves today.  
> Step 1 (“Current State”) remains the source of truth for existing behavior.

---

## 1. Goals, Non-Goals, and Invariants

### 1.1 Primary Goals

1. **Eliminate ASR “no audio” timeouts by design**  
   - Never open a Deepgram streaming STT session unless we already have real audio to send.
   - Vendor timeouts become rare error conditions, not part of normal UX.

2. **Keep continuous audio into VAD, but not into ASR**  
   - Mic and audio graph run continuously (warm handoff).
   - VAD always “sees the world” so it can track noise floor and detect speech reliably.
   - ASR only sees buffered + live speech for actual user turns.

3. **Separate gates cleanly (no more gate hairball)**  
   - **Hard gate:** “Is it even legal to send user audio right now?” (phase, WS, server holds, fatal errors).
   - **Soft gate (VAD silencer):** “Given that sending is legal, should we send *this frame* or drop it?”
   - **Policy layer:** “Given what just happened, what should Chip do?” (nudge, barge-in confirmation, retries).

4. **Make greet → conversation smooth and predictable**  
   - Greet never causes vendor timeouts.
   - The first user utterance after greet is fully captured (no clipped first word).
   - If the user doesn’t speak, they get a single, polite nudge; nothing else explodes.

5. **Barge-in is supported without destabilizing the pipeline**  
   - User can speak over Chip (during greet or TTS).
   - System resolves barge-in with clear rules and minimal state churn.

### 1.2 Non-Goals

- No attempt to change **HTTP/WS surface paths** (`/ws/v2/chat`, `/api/v1/...`) or routing – these remain stable.
- No change to high-level product features (login, profiles, admin UI) beyond what’s needed to support the new flow.
- No attempt to optimize cost or latency beyond what naturally falls out of the new lifecycle; that can be a later step.

### 1.3 Global Invariants (Target)

These are the things we want to be **true 100% of the time** in the V3 design:

1. **Invariant A – No empty vendor streams**  
   > If a Deepgram streaming STT session is open, it is receiving audio frames “close to real time.”

   - Corollary: We never open a vendor stream while the client is gated from sending PCM.

2. **Invariant B – VAD always has continuous PCM**  
   > Mic → audio graph → VAD is continuous while the AskChip UI is active.

   - We do **not** repeatedly start/stop the mic for each turn.
   - VAD uses this continuous flow to maintain noise floor and speech detection.

3. **Invariant C – Greet and vendor ASR are decoupled**  
   > Greet TTS does **not** depend on a Deepgram streaming session and cannot cause a vendor timeout on its own.

4. **Invariant D – First utterance is fully captured**  
   > For each user turn, the stream sent to the vendor includes a small pre-roll (e.g., 500–800 ms) so the first word isn’t clipped.

5. **Invariant E – ASR availability ≠ ASR stream open**  
   > “ASR available” means “we can open a vendor stream immediately on speech,” not “vendor stream is already open.”

---

## 2. High-Level Conceptual Overview (Deepgram Flow V3)

Under Deepgram Flow V3, AskChip behaves like this:

- The **browser mic and audio graph come up once** and stay live.
- VAD and a **ring buffer** continuously see PCM at the target ASR rate (e.g., 16 kHz mono).
- When the user speaks:
  - VAD detects speech.
  - We open a Deepgram streaming session *at that moment*.
  - We immediately send the buffered pre-roll (last 500–800 ms), then live frames.
- When the user stops speaking:
  - We end the turn locally (silence-based or explicit stop).
  - We send any final frames, then close the Deepgram stream promptly.
- If the user **never speaks**:
  - No Deepgram streaming session is ever opened.
  - A local client/server timer triggers a single “hey, I’m ready when you are” nudge.

The result is:

- VAD sees continuous audio.
- Deepgram only sees real audio for actual utterances.
- Greet, nudges, and barge-in all ride on top of this clean lifecycle.

---

## 3. Target Runtime & Deployment Model (What Changes, What Stays)

### 3.1 Surfaces and Deployment (Mostly Unchanged)

- **ASGI entrypoint**, HTTP routes, and `/ws/v2/chat` WebSocket endpoint remain the same externally.
- **Auth, admin, static serving, and log export** behavior remain as defined in Step 1.
- **Environment variables** and config model remain, but we introduce a small set of **new, explicit knobs** for Deepgram Flow V3 (see §8).

### 3.2 Behavioral Changes at a High Level

1. **Server-side ASR lifecycle changes:**
   - Stream opening is moved from “greet end / conversation ready” to “first speech detected (with pre-roll)”.
   - Greet completion no longer directly triggers vendor ASR open.
   - No vendor streams are left idle waiting for user audio.

2. **Client-side audio/gating changes:**
   - Hard gate is simplified: phase + WS + server hold.
   - VAD silencer is moved **after** VAD, as a per-frame filter, and is not expressed as a pauseReason.
   - Nudges and timers hook into phase and VAD state, not vendor error codes.

3. **Barge-in changes:**
   - Barge-in detection uses VAD + TTS state.
   - When barge-in is granted:
     - TTS is stopped/cancelled.
     - A new ASR stream is opened with pre-roll from the ring buffer.
     - Gating remains stable; we don’t tear down the entire audio graph.

---

## 4. Target Server-Side Design (Deepgram Flow V3)

### 4.1 Conversation & Turn States (Refinement of EngineV2)

We keep the existing high-level state machine:

- `READY`
- `LISTENING`
- `THINKING`
- `RESPONDING`
- `CONFIRMING_BARGE`

…but **we tighten the semantics for Deepgram Flow V3**:

- **READY**
  - No active vendor ASR stream.
  - No active TTS for the current turn.
  - Engine is prepared to:
    - start greet (for new sessions), or
    - begin LISTENING once user speech arrives.
- **LISTENING**
  - Exactly one vendor ASR stream is open *for this user turn*.
  - Engine is accepting PCM frames and forwarding to the vendor.
- **THINKING**
  - Vendor ASR stream is closed.
  - Final hypothesis has been committed.
  - LLM / policy is computing the next response.
- **RESPONDING**
  - TTS is active and streaming audio back to the client.
  - Engine may accept barge-in events, depending on policy.
- **CONFIRMING_BARGE**
  - TTS is being stopped/cancelled and a new user turn is being prepared.
  - A new LISTENING state for the barge-in utterance follows if barge is granted.

The **key change**:  
In V3, *READINESS* to listen does not automatically imply **LISTENING (vendor streaming) has started**. LISTENING only begins when we have actual user speech plus pre-roll (see below).

### 4.2 ASR Lifecycle (Deepgram Flow V3)

#### 4.2.1 When we open a vendor stream

In V3, the server opens a Deepgram streaming STT session **only when**:

1. A `user_turn_start` event is raised from the client (explicit or implicit), and  
2. The event includes:
   - a flag indicating “speech detected,” and
   - an initial chunk of buffered PCM (pre-roll).

Conceptually:

- The client sends a **`turn_start` control message** (or a well-defined variant) that includes:
  - `turn_id`
  - `pre_roll_ms`
  - optional metadata (VAD confidence, RMS snapshot).
- Immediately following this message, the client starts sending PCM frames for Turn N.
- On the first PCM for Turn N, the adapter:
  - verifies policy and session state,
  - opens the Deepgram stream,
  - writes the initial frames (pre-roll + first live frames),
  - records `asr_stream_id` and sets state to `LISTENING`.

#### 4.2.2 While the stream is open

While in `LISTENING`:

- The adapter expects a **continuous sequence of PCM frames** for that turn, with allowed jitter.
- The adapter maintains:
  - `last_audio_at_ms`
  - byte counters, sequence numbers, and metrics for diagnostics.

Safety nets:

- If no frames arrive for **X ms** during LISTENING:
  - Server assumes a network stall or client-side error.
  - Vendor stream is closed with a “no-audio” error.
  - Engine transitions to READY and emits a recoverable error to the client.
- The client is still responsible for **VAD-based turn end** and closing the stream promptly when silence is detected.

#### 4.2.3 When we close a vendor stream

The server closes the stream when:

- A `turn_stop` / `turn_end` control message is received from the client, or
- The server has already produced a final ASR result for the turn, or
- A safety net (timeout, transport error) fires.

On normal close:

- Deepgram stream is closed cleanly.
- Engine transitions from LISTENING → THINKING → RESPONDING.
- Metrics and logs are produced:
  - `asr_bytes_from_client`
  - `asr_bytes_to_vendor`
  - timing breakdowns.

On error close:

- Engine transitions to READY.
- An error message is sent back to the client with a **clear, typed reason** (`asr_timeout`, `network_error`, etc.).

### 4.3 Greet and Conversation Ready (Server View)

- Greet continues to be orchestrated in `EngineV2`:
  - LLM constructs greet content.
  - TTS streams greet to client.
- **Vendor ASR is *not* opened purely because greet finished.**
  - Instead, the server moves the session into a **“Conversation Ready”** logical state:
    - ready to open a new ASR stream,
    - but with no stream actually open yet.
- “Conversation Ready” is conveyed to the client via a dedicated control message (e.g., `server.conversation_ready` or existing equivalent).

### 4.4 Barge-in (Server View)

For Deepgram Flow V3:

- While in RESPONDING (TTS active), the server is willing to accept **barge-in** signals from the client:
  - A barge-in signal is raised when the client detects sustained speech over TTS and local policy permits interruption.
- On barge-in:
  - TTS is stopped/cancelled.
  - Engine transitions to `CONFIRMING_BARGE`.
  - If policy grants barge-in, Engine moves to READY and then LISTENING for a **new turn** using the same ASR lifecycle as above.

---

## 5. Target Client-Side Design (Deepgram Flow V3)

### 5.1 Phases and State

We retain the core client phases:

- `boot`
- `greet`
- `conversation_ready`
- `user_turn`
- `closing`
- `closed`

…and we align them precisely with the server-side lifecycle:

- `greet`  
  - TTS greet is playing.
  - Hard gate blocks sending any PCM to the server.
  - Mic and VAD are already live and observing audio.
- `conversation_ready`  
  - Greet is done.
  - Hard gate allows sending PCM (subject to other gates).
  - No vendor ASR stream is open yet.
- `user_turn`  
  - A vendor ASR stream is open for the current turn.
  - Client is streaming PCM for this turn.

### 5.2 Audio Pipeline with Ring Buffer

Target client pipeline:

```text
Mic (getUserMedia)
    ↓
Capture graph (resample to 16 kHz mono, gain, filters)
    ↓
Ring buffer (preSpeechBufferMs ≈ 500–800 ms)
    ↓
VAD (continuous input)
    ↓
Soft gate (per-frame: send vs drop)
    ↓
WS PCM sender

Key points:

Ring buffer:

Always stores the last preSpeechBufferMs worth of frames (e.g., 0.5–0.8 seconds).

When we detect speech (see below), we mark a turn and send:

the ring buffer contents as pre-roll,

then live frames.

VAD:

Receives all PCM frames (no gating before it).

Produces events such as:

speech_start

speech_end

RMS/energy reports for logging and policy.

5.3 Three-Layer Gating Model (Client)
5.3.1 Hard gate (“is it legal to send at all?”)

Inputs:

wsPhase ∈ {connected, ready}

AppState.phase ∈ {conversation_ready, user_turn}

serverHold / systemHold flags

fatal errors (mic permanently failed, etc.)

Behavior:

If any hard-gate condition fails:

We do not send any PCM frames to the server.

We never attempt to open a vendor ASR stream for that turn.

VAD and ring buffer continue to run even when the hard gate is closed.

5.3.2 Soft gate / VAD silencer (“this frame vs drop”)

Inputs:

VAD output (isSpeech, isProbablySpeech, rms)

speechSeenThisTurn (local per-turn state)

turnActive (client view of current user turn)

thresholds:

speechStartRmsThreshold

silenceEndMs

Behavior:

Before speechSeenThisTurn = true:

We drop low-energy frames (noise floor) on the floor.

We do not open an ASR stream.

When VAD crosses the speech threshold for N consecutive frames:

We set speechSeenThisTurn = true.

We mark the start of a user turn.

We send a turn_start control message.

We begin streaming:

ring buffer pre-roll frames,

then live frames.

After speechSeenThisTurn = true:

We continue sending frames until we detect sustained silence (silenceEndMs).

When silence persists:

We send a turn_stop message.

We stop sending frames for this turn.

Crucially:

The soft gate does not control pauseReasons or baseEnabled.

It only decides “send this frame vs drop this frame” within the scope of an active turn.

5.3.3 Policy Layer (nudges, barge-in, UX actions)

Inputs:

phase / wsPhase

speechSeenThisTurn

timeSinceGreetEnd

timeSinceLastUserSpeech

TTS-active flag

Key behaviors:

Greet nudge:

Once we enter conversation_ready, we start a local “no speech since greet” timer.

If speechSeenThisTurn is still false after GREET_SILENCE_NUDGE_MS (e.g., 5–8 s):

Show a single “Hey, I’m ready when you are” nudge.

Do not open ASR just for the nudge.

Do not spam multiple nudges.

In-conversation nudges (optional):

After a long idle period in conversation_ready with no speech:

Optional single “Need help with anything else?” nudge.

Rate-limited and controlled by config.

Barge-in:

While TTS is playing:

If VAD detects sustained speech above a barge-in threshold:

Client sends a barge_request control message with optional audio stats.

Server decides whether to grant barge-in.

On grant:

Client stops TTS playback.

Phase moves to conversation_ready or directly user_turn (depending on server response).

A new user turn begins using the same ring buffer + ASR lifecycle.

6. Key Flow Narratives (Target Behavior)
6.1 Initial Page Load → Greet → First User Turn

Page load

AppState.phase = greet.

WS connection opens (wsPhase = connected → ready).

Mic is acquired; audio graph and VAD start running; ring buffer is already filling.

Greet

Server sends greet TTS; client plays audio.

Hard gate blocks PCM sending (phase = greet).

VAD still observes audio; we ignore it for ASR purposes.

Conversation ready

Greet ends; server sends conversation_ready.

AppState.phase = conversation_ready.

Hard gate now allows PCM sending, but:

We have not opened a vendor ASR stream.

Soft gate waits for speech.

User starts speaking

VAD detects sustained speech:

speechSeenThisTurn = true.

Soft gate declares a user turn.

Client sends:

turn_start control message (includes turn_id and pre-roll metadata).

Immediately streams ring-buffer PCM (pre-roll) followed by live frames.

Server opens ASR

On receipt of Turn N’s first PCM frames, server:

opens a Deepgram streaming STT session,

writes pre-roll + live frames,

moves EngineV2 state to LISTENING.

User stops speaking

VAD detects sustained silence for silenceEndMs.

Client sends:

remaining frames,

turn_stop control message.

Soft gate stops sending frames for Turn N.

ASR result and response

Server closes ASR stream on vendor final.

Engine transitions LISTENING → THINKING → RESPONDING.

TTS output is streamed to the client.

Client plays TTS; AppState.phase = user_turn during TTS, then back to conversation_ready on TTS end.

6.2 User Does Not Speak After Greet

Greet finishes; phase = conversation_ready.

Soft gate never sees speechSeenThisTurn = true.

No turn_start is sent; no vendor ASR stream is opened.

When timeSinceGreetEnd > GREET_SILENCE_NUDGE_MS:

Client (or server policy) shows exactly one nudge (“I’m ready when you are”).

Still no speech:

No vendor ASR timeout; the system is idle but stable.

Optional: after a longer timeout, UI can show “Chip is standing by” or similar.

6.3 Barge-in During Greet

During greet, TTS is playing (phase = greet).

VAD detects sustained speech above barge threshold.

Client sends barge_request (no ASR stream open yet).

Server grants barge:

Cancels remaining greet TTS.

Sets session to conversation_ready.

Client transitions to conversation_ready.

User continues speaking; ring buffer + VAD detect new speech.

Flow continues as in 6.1 (turn_start → ASR open → TTS response).

6.4 Subsequent Turns in a Long Conversation

All subsequent turns behave like the first turn, but without greet:

Phase oscillates between conversation_ready ↔ user_turn.

Ring buffer, VAD, and three-layer gating model remain active throughout.

Vendor ASR is opened and closed per turn, never left idle.

7. Config Knobs and Defaults (Deepgram Flow V3)

Introduce or formalize the following configuration knobs (names illustrative):

DEEPGRAM_V3_PRE_SPEECH_BUFFER_MS

Default: 700 ms

Range: 300–1500 ms

Controls ring buffer size for pre-roll.

DEEPGRAM_V3_SPEECH_START_MIN_FRAMES

Default: 5 frames

Minimum consecutive “speech” frames before we declare turn start.

DEEPGRAM_V3_SILENCE_END_MS

Default: 800 ms

Silence duration after last speech before we auto-end the turn.

DEEPGRAM_V3_GREET_SILENCE_NUDGE_MS

Default: 7000 ms

Time after greet end with no speech before we show nudge.

DEEPGRAM_V3_BARGE_IN_ENABLED

Default: true

Enables barge-in behavior as described.

DEEPGRAM_V3_ENABLED

Master feature flag to keep current behavior available as fallback during rollout.

These can live alongside existing config values and gradually move from experimental → default once stabilized.

8. Observability & Acceptance Criteria
8.1 Observability

For Deepgram Flow V3, we want explicit, low-noise instrumentation around:

ASR lifecycle events

deepgram_v3.asr_open

deepgram_v3.asr_close

deepgram_v3.asr_no_audio_safety_net_fired (should rarely be true)

Per-turn byte counts and timing.

Client gating snapshots

Periodic, rate-limited logs when:

speechSeenThisTurn = true but no bytes sent.

Hard gate denies sending while VAD reports speech.

Nudge events

deepgram_v3.nudge.greet_silence

deepgram_v3.nudge.idle_conversation

Barge-in

deepgram_v3.barge.requested

deepgram_v3.barge.granted

deepgram_v3.barge.denied

8.2 Acceptance Criteria

Deepgram Flow V3 is considered “done” when:

No more vendor “Audio Timeout Error” for quiet users

If a user remains silent after greet, we see:

zero vendor streams opened, or

streams opened only during actual speech.

Any remaining timeouts are due to genuine network failures.

First words are never clipped

In logs and manual testing, transcripts always contain the full first word (and pre-roll) of user utterances.

Greet → conversation is stable and predictable

No cases where:

greet plays but PCM is permanently gated after,

user speech is ignored because of stale pauseReasons.

Barge-in works without destabilizing the session

User can interrupt greet and long TTS responses.

After barge-in, conversation continues with the same VAD + ring buffer model.

Nudges are present but not spammy

At most one post-greet nudge per greet.

Optional follow-up nudges are controlled and rate-limited.

9. Summary

Deepgram Flow V3 re-architects AskChip’s speech pipeline around three principles:

Vendor streams are only open when we have audio to send.

VAD sees a continuous, unbroken audio world.

Gating and policy are separated so we don’t re-live the same race conditions.