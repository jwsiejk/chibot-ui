# Full‑Duplex Conversational Flow — Golden Path (Phase 1)

> **Objective:** Ship a reliable, low‑latency, full‑duplex voice loop by simplifying to one golden path: **mic + socket + !TTS ⇒ stream**. Eliminate UI/turn coupling, policy mazes, phase gating, and premature watchdogs.

---

## 1) Golden Path (happy‑day) — State & Order of Operations

**States:** `WS_OPEN` → `ASR_READY` → `CAPTURE_ON` → `FIRST_PCM` → `LISTENING`

**Must‑see breadcrumbs (in order):**

1. `client.turn.intent {action:"open"}` *(optional but recommended)*
2. `client.pcm.capture_start`
3. `client.audio_header_send`
4. `client.pcm.first_frame`
5. (silence) `client.vad.gate pause` *(badge remains Listening; hint "quiet (VAD)")*
6. (speech) `client.vad.speech_start` → bursts of `client.audio_chunk_send`

**Server:** `asr_ready_bundle_sent` → `RecognitionStarted` → partials/finals (no `asr_no_audio_after_header`).

---

## 2) Client Responsibilities (authoritative rules)

* **Always‑on capture:** If **WS is OPEN** and **TTS is not active**, ensure mic capture is on.
* **Idempotent mic start:** `AudioRecorder.startMicCapture()` may be invoked repeatedly; it must be re‑entrant, single‑stream, and resolve success if already listening.
* **Single GUM:** Guard against duplicate `getUserMedia`; cache one stream.
* **Open‑socket fast‑paths:**

  * Control frames (`input.start`, `input.stop`, `audio.header`, `ping/pong`) send immediately when socket is **OPEN**.
  * PCM sends when socket is **OPEN** (no phase gating).
* **Arm before gate:** Allow first PCM (or ≤1.2s grace) to pass before VAD/flags can block.
* **Throttle (server hint):** On `{type:"audio.throttle", ms}`, pause PCM for `ms`, then resume.

---

## 3) UI / Status Bar — Truthful, Calm, Helpful

**Badge mapping:**

* `Speaking…` when TTS active.
* `Preparing mic…` when mic open but ASR not ready.
* **`Listening`** when `(micLive || listening) && asrReady && !tts`. Never demote due to `activeTurn`.
* Hints:

  * `quiet (VAD)` when VAD is paused.
  * *(optional)* `opening turn…` as a brief hint until `asr.turn begin` arrives.
* `Thinking…` from `asr.final` until `tts.start` (or first assistant token / timeout).

---

## 4) Policy / Autostart — Simple & Predictable

* Defaults: `start_on_asr_ready !== false`, `start_on_turn_ready !== false`.
* Trusted triggers override policy: `'asr_ready' | 'turn_begin' | 'await_user'`.
* `maybeAutoStartCapture()` behavior: If socket is OPEN and not TTS, ensure capture; otherwise no‑op.

---

## 5) Watchdog — Silence‑Only (No Premature Stops)

* **Arm** only when: capture is on, turn is active, first PCM seen, and either speech active or ≥ a small chunk threshold.
* **Timeout handling:** If **no bytes** and **no partials** for ≥8s (and not TTS), **log** `watchdog_silence_notice` and reschedule; do **not** stop a live mic.

---

## 6) VAD / Audio Capture Settings

* **WebRTC constraints:** `echoCancellation:true`, `noiseSuppression:true`, `autoGainControl:false` *(default)*, `channelCount:1`, `sampleRate:48000`, `sampleSize:16`.
* **AGC policy toggle:** Allow runtime enable via `AppState.policy.media.agc=true` if RMS remains too low.
* **VAD defaults (client):** `stream_gate:"gate"` *(or "none" if bandwidth isn’t a concern)*, `sensitivity≈0.60`, `min_speech_ms≈160`, `min_silence_ms≈300`.
* **First‑frame grace:** Do not let VAD prevent the very first PCM.

---

## 7) Server Adapter Expectations (for backend owners)

* **No silent drops:** Replace `backpressure_drop` with a 250–350 ms ring buffer or client **throttle** message.
* **Throttle frame:** `{type:"audio.throttle", ms:200}` instructs client to pause sending PCM briefly.
* **Vendor feed:** Maintain steady PCM to ASR; avoid “header‑only” recognition.

---

## 8) Acceptance Criteria — One‑Run Bring‑Up

**Client log order:**

1. `client.pcm.capture_start` → 2) `client.audio_header_send` → 3) `client.pcm.first_frame` → 4) `client.vad.speech_start` on speech.

**UI:** Badge = **Listening** after greet; shows `quiet (VAD)` during silence; `Thinking…` between `asr.final` and `tts.start`.

**Server:** No `asr_no_audio_after_header`; partials appear on speech.

**Wire:** No `WSClient.send queued (phase not ready)` for control frames; PCM flows whenever WS is OPEN.

---

## 9) Runtime Toggles / Observability

* **Toggles (default false):** `AppState.debug = { audio_safe_mode:false, force_capture:false }`.
* **AGC override:** `AppState.policy = { media: { agc:true } }` (effective on next start).
* **Key breadcrumbs:** `client.turn.intent`, `client.pcm.capture_start`, `client.pcm.first_frame`, `client.vad.gate`, `client.audio_chunk_send`, `watchdog_silence_notice`, `client.audio.throttle`.

---

## 10) Rollback & Safety

* Temporarily bypass gating: `AppState.policy.vad.client.stream_gate = "none"`.
* Emergency stream: `AppState.debug.force_capture = true` (debug only).

---

### Phase 1 Deliverable (this doc)

* A single source‑of‑truth reference for the *full‑duplex golden path* that engineering, QA, and backend can follow.
* Basis for Phase 2+ Codex patches that implement/lock these behaviors in code.

> **Next:** Proceed to Phase 2 Codex patches (wire fast‑paths + idempotent mic start) using this doc as the spec.
