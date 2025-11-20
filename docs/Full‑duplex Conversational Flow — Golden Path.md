# Full-Duplex Conversational Flow — Golden Path (Authoritative Spec)

> **Objective:** Define the complete, authoritative behavioral spec for AskChip’s real-time conversational loop:
> 
> - One unified turn model for **voice + text**.
> - A **full-duplex** audio path with **soft, echo-aware barge-in**.
> - Clear, boringly predictable **UI states** and **logging**.
> - No phase gating, no “modes,” no hidden watchdogs.

This document is the single source of truth for how the client, UI, and server adapter are expected to behave. Code should conform to this spec; if the code disagrees, the spec wins.

---

## 1) Global Mental Model

There is **one** conversation loop that supports two input modalities:

- **Voice turns** – mic + VAD + ASR.
- **Text turns** – typed messages.

Both feed the **same turn state machine**:

- `READY` → `LISTENING` → `THINKING` → `SPEAKING` → `READY`

and share the same logging and UI semantics.

### States (voice-oriented view)

For the *audio* layer we further break out:

- `WS_OPEN` → `ASR_READY` → `CAPTURE_ON` → `FIRST_PCM` → `LISTENING`

These are internal milestones; the **user-facing badge** simplifies this to the four top-level states.

---

## 2) Golden Path — Voice Turn (Happy Day)

### State & Order of Operations

**Internal states:**  

`WS_OPEN` → `ASR_READY` → `CAPTURE_ON` → `FIRST_PCM` → `LISTENING`

**Must-see breadcrumbs (in order):**

1. `evt=client.turn.intent` `{ action:"open" }` *(optional but recommended)*
2. `evt=client.pcm.capture_start`
3. `evt=client.audio_header_send`
4. `evt=client.pcm.first_frame`
5. (silence) `evt=client.vad.gate` `"pause"` *(badge remains Listening; hint "quiet (VAD)")*
6. (speech) `evt=client.vad.speech_start`
7. Bursts of `evt=client.audio_chunk_send` while user talks
8. `evt=client.asr.partial` (0–N times)
9. `evt=client.asr.final`
10. `evt=client.tts.start`
11. `evt=client.tts.end`

**Server side (conceptual):**

- `asr_ready_bundle_sent`
- `RecognitionStarted`
- `RecognitionPartial` / `RecognitionFinal` events
- No `asr_no_audio_after_header`

---

## 3) Unified Turn Model (Voice + Text)

### Turn lifecycle

Every user turn (voice *or* text) follows:

1. **READY → LISTENING**
   - Voice:
     - Mic capture is on, ASR is ready, VAD is armed.
     - Badge: **Listening**
   - Text:
     - User has focus in the input; badge is still **Ready** until they send.

2. **LISTENING → THINKING**
   - Voice:
     - VAD detects end of speech (or ASR timeout treated as end).
     - Client sends any `input.stop` / “end turn” control if required.
     - Badge: **Thinking…**
   - Text:
     - User hits send; we immediately:
       - Log `evt=client.text.send`
       - Show user bubble
       - Set badge: **Thinking…**

3. **THINKING → SPEAKING**
   - Server runs LLM; when first assistant token / TTS audio arrives:
     - Log `evt=client.tts.start` (+ optional `evt=client.chat.stream_start`)
     - Badge: **Speaking…**
   - The assistant reply may be streaming text + streaming TTS.

4. **SPEAKING → READY / LISTENING (auto)**
   - When TTS finishes:
     - `evt=client.tts.end`
     - Badge returns to **Listening** (voice) or **Ready** (if mic disabled).
   - For the golden path with autolistening:
     - We immediately ensure mic capture is active and ASR is re-armed.
     - User should feel: “Chip finished → I can talk again.”

### Invariants

- Voice and text:
  - **Share the same backend contract** for turns.
  - Use the same **turn state** and **badge mapping**.
  - Differ only by how they produce the user’s input content.

---

## 4) Client Responsibilities (Authoritative Rules)

### 4.1 Always-on Capture & Idempotent Start

- If **WS is OPEN** and **TTS is not active** and **voice is enabled**:
  - Mic capture MUST be active (or starting).
- `AudioRecorder.startMicCapture()`:
  - Is **idempotent** and **re-entrant**.
  - If capture already running, resolves success and does not spawn a second stream.
- **Single GUM (`getUserMedia`)**:
  - Guard against duplicate calls.
  - Cache the `MediaStream` and reuse it until closed.

### 4.2 Open-Socket Fast-Paths

Once the WebSocket is `OPEN`:

- **Control frames**:
  - `input.start`, `input.stop`, `audio.header`, `ping`, `pong`, “turn begin/end”, etc.
  - Must send **immediately** when socket is OPEN.
  - No “queued (phase not ready)” semantics for control.

- **PCM frames**:
  - Must send as soon as:
    - Socket is OPEN, and
    - Audio header has been accepted, and
    - Any active `audio.throttle` has expired.
  - No additional phase gating in the client.

### 4.3 Arm Before Gate

- The first PCM bytes after `client.audio_header_send` must be allowed through; VAD cannot block the very first frame.
- Implementation:
  - Either:
    - Special-case “first frame bypass” for VAD, or
    - Apply a grace window (e.g., ≤ 1.2s) where PCM flows regardless of VAD gate state.

### 4.4 Throttle (Server Hint)

- On `{type:"audio.throttle", ms}` from server:

  - Client MUST:
    - Log `evt=client.audio.throttle` `{ ms }`.
    - Pause PCM sending for `ms` milliseconds.
    - Resume normal sending after `ms` without restarting mic capture.
  - The mic **stays on**; only sender is paused.

---

## 5) Barge-In (Soft, Echo-Aware)

Barge-in allows the user to interrupt Chip while he’s speaking.

### 5.1 Input Rules

- While TTS is active, mic capture remains **on** but:
  - PCM sending is **gated** by barge-in policy.
- Barge-in is committed only when:
  - VAD detects speech **above an echo-aware threshold**, **and**
  - Either:
    - A wake phrase is recognized (e.g., “Hey Chip…”, “Chip, hold on”), or
    - The user has sustained speech for ~420 ms without dropping back to silence.

### 5.2 Client Behavior on Barge-In

When barge-in is committed:

1. **Stop/soft-stop TTS**
   - Log `evt=client.bargein.commit`.
   - Invoke TTS stop/pause on the player.
   - Badge flips to **Listening** quickly.

2. **Open new user turn**
   - Ensure any required `input.stop` / “interrupt” control frame is sent for the prior turn.
   - Start a new turn for the user:
     - Log `evt=client.turn.intent` `{ action:"bargein" }`.
     - Keep mic capture running (no restart).

3. **Send PCM for new turn**
   - Same rules as the golden path:
     - Header (if needed) → `client.audio_header_send`
     - `client.pcm.first_frame`
     - `client.vad.speech_start`
     - PCM bursts.

### 5.3 Keyboard / ESC Shortcut

- ESC key is a **hard immediate interrupt**:
  - Immediately stops TTS.
  - Ends sending any further PCM for the current turn.
  - Solid log: `evt=client.bargein.esc`.
  - Leaves mic capture on and transitions badge to **Listening** for the next turn.

### 5.4 Guardrails

- Barge-in must be **echo-aware**:
  - VAD and thresholds must be tuned so Chip’s own voice does not regularly trigger barge-in.
- Client should back off if:
  - Multiple barge-ins are ignored at the server level.
  - A policy toggle can disable barge-in entirely if needed.

---

## 6) UI / Status Bar — Truthful, Calm, Helpful

The status bar is the single visual truth for the conversation state.

### 6.1 Badge Mapping

- **Ready**
  - Default, no active turn, mic may or may not be on.
  - Text input idle.

- **Listening**
  - Voice:
    - `(micLive || listening) && asrReady && !tts`.
    - Never demoted due to internal “activeTurn” flags.
  - Text:
    - Can optionally show “Listening” while user is recording a voice note or dictating.

- **Thinking…**
  - From `asr.final` (or text send) until:
    - `tts.start`, or
    - First assistant text token if no TTS.

- **Speaking…**
  - While TTS is active.
  - Ends on `client.tts.end`.

### 6.2 Hints

Hints are short, quiet overlays under the main badge:

- `quiet (VAD)` when VAD gate is paused but mic is on.
- `preparing mic…` when mic is open but ASR not ready.
- `opening turn…` between `input.start` and `RecognitionStarted`.

### 6.3 After Greet

- After greet TTS finishes:
  - Badge must return to **Listening** automatically (for voice) with mic active and ASR ready.
  - No extra button press should be required for the user to start talking.

---

## 7) Policy / Autostart — Simple & Predictable

- Defaults:
  - `start_on_asr_ready !== false`
  - `start_on_turn_ready !== false`
- Trusted triggers override policy:
  - `'asr_ready' | 'turn_begin' | 'await_user'`.
- `maybeAutoStartCapture()`:
  - If socket is OPEN and TTS is not active, ensure capture is running.
  - Otherwise, it is a no-op; it never *stops* capture.

---

## 8) Watchdog — Silence-Only (No Premature Stops)

### 8.1 Arming

Watchdog arms only when:

- Capture is on.
- Turn is active.
- First PCM has been seen.
- Either:
  - Speech has started at least once, or
  - A minimum chunk threshold has been sent.

### 8.2 Behavior

If **no bytes** and **no ASR partials** for ≥ 8s (and not in TTS):

- Log `evt=watchdog_silence_notice` with meta:
  - `{ lastPcmMs, lastPartialMs, turnId, sid }`.
- Optionally nudge the UI (“Chip is still listening, try speaking again.”).
- **Do not stop mic capture**.
- Reschedule the watchdog.

No other watchdog should turn off the mic or close the socket.

---

## 9) VAD / Audio Capture Settings

- **WebRTC constraints (getUserMedia):**
  - `echoCancellation: true`
  - `noiseSuppression: true`
  - `autoGainControl: false` *(default)*
  - `channelCount: 1`
  - `sampleRate: 16000`
  - `sampleSize: 16`

- **AGC policy toggle:**
  - Allow runtime enable via `AppState.policy.media.agc = true` if RMS remains too low.

- **VAD defaults (client):**
  - `stream_gate: "gate"` *(or `"none"` if bandwidth isn’t a concern)*
  - `sensitivity ≈ 0.60`
  - `min_speech_ms ≈ 160`
  - `min_silence_ms ≈ 300`

- **First-frame grace:**
  - VAD must not prevent the very first PCM.

---

## 10) Server Adapter Expectations

- **No silent drops:**
  - Replace `backpressure_drop` with:
    - A 250–350 ms ring buffer, or
    - Client **throttle** messages.

- **Throttle frame:**
  - `{type:"audio.throttle", ms:200}` instructs the client to pause PCM briefly.

- **Vendor feed:**
  - Maintain a steady PCM stream to ASR.
  - Avoid “header-only” recognition scenarios.

- **Turn semantics:**
  - Gracefully handle barge-in: when an interrupt is signaled, end the current turn and accept a new one without requiring a full WS reconnect.

---

## 11) Logging & Observability

### 11.1 Core Breadcrumbs (Voice)

Minimum logs per healthy voice turn:

- `evt=client.pcm.capture_start`
- `evt=client.audio_header_send`
- `evt=client.pcm.first_frame`
- `evt=client.vad.gate`
- `evt=client.vad.speech_start`
- `evt=client.audio_chunk_send`
- `evt=client.asr.partial` / `evt=client.asr.final`
- `evt=client.tts.start`
- `evt=client.tts.end`

### 11.2 Text

- `evt=client.text.send` with `{ text_len, turnId }`.
- `evt=client.chat.stream_start`
- `evt=client.chat.stream_end`

### 11.3 Errors / Warnings

At minimum:

- `evt=client.asr_no_audio_after_header`
- `evt=client.ws.error`
- `evt=client.ws.close`
- `evt=client.bargein.commit`
- `evt=client.bargein.esc`
- `evt=watchdog_silence_notice`

All events should include `{ sid, turnId, ts, ... }` where available.

---

## 12) Acceptance Criteria — End-to-End

For a **healthy conversation**:

1. **After greet TTS:**
   - Badge = **Listening**.
   - Mic is active, ASR ready.
   - Logs show a full greet reply (`tts.start` → `tts.end`).

2. **First voice question:**
   - Log order includes:
     - `client.pcm.capture_start`
     - `client.audio_header_send`
     - `client.pcm.first_frame`
     - `client.vad.speech_start`
   - Server shows no `asr_no_audio_after_header`.
   - UI shows **Listening → Thinking → Speaking → Listening** in that order.

3. **Text turns:**
   - Sending text logs `evt=client.text.send`.
   - UI shows **Thinking → Speaking → Ready/Listening** with the same semantics as voice.

4. **Barge-in:**
   - While Chip is speaking, sustained user speech:
     - Produces `evt=client.bargein.commit`.
     - Stops TTS quickly.
     - Opens a new user turn and shows **Listening**.

5. **Watchdog:**
   - No watchdog event ever stops the mic or closes WS.
   - `watchdog_silence_notice` only logs and optionally nudges.

6. **Wire:**
   - No `WSClient.send queued (phase not ready)` for control frames.
   - PCM flows whenever WS is OPEN and not throttled.

---

## 13) Runtime Toggles & Rollback

- **Debug toggles (default `false`):**
  - `AppState.debug = { audio_safe_mode: false, force_capture: false }`.

- **AGC override:**
  - `AppState.policy = { media: { agc: true } }` (effective on next capture start).

- **Rollback / safety levers:**
  - Temporarily bypass gating:
    - `AppState.policy.vad.client.stream_gate = "none"`.
  - Emergency capture (debug only):
    - `AppState.debug.force_capture = true`.

These levers are intended for **debugging and rollback only**. The golden path described above is the expected default behavior.

---
