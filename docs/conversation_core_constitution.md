# AskChip Conversation Core Constitution

This constitution defines the non-negotiable rules for the conversational core: the audio transport and turn engine that sit between the browser mic and the server-side speech/LLM stack. It does **not** set UI layout, persona, admin UI, or LLM/TTS policies.

## Scope

This document applies to:

- WebSocket mic audio lane.
- Server audio buffering / "mailbox" behavior.
- Turn engine ownership (who opens/closes ASR; who owns user turns).
- Client PCM sender / VAD gating behavior.

It explicitly does **not** cover UI layout, persona/voice choices, admin tools, or LLM/TTS implementation details.

## Core principles

- Audio transport should be dumb, tolerant, and always-accepting.
- Turn semantics must live in a single place on the server.
- The client must not try to mirror or predict server ASR state.
- Every user turn should have an observable, end-to-end lifecycle.

## Hard invariants

The following invariants are mandatory for any future work. Refer to them as INV-1 … INV-5.

### INV-1: Mailbox / Always-Buffer Rule

- The WebSocket adapter must always accept binary mic audio frames for the mic lane and ingest them into a bounded ring buffer.
- `_handle_binary` (or its v2 equivalent) must:
  - Update ingress metrics (packets, bytes, `last_activity_ms`).
  - Push audio into the buffer with a sequence number.
  - **Never** reject mic audio with `audio_not_expected` or similar state-based errors.
- Decisions about whether audio is “expected” or usable are made **after** buffering by the turn engine, not at packet ingest.
- The buffer must be bounded in time/bytes, dropping the oldest audio on overflow and logging a `buffer_overflow`-style event.

### INV-2: Single Source of Turn Truth (TurnEngine Ownership)

- Exactly one server component (e.g., TurnEngine) owns:
  - Opening and closing ASR streams.
  - Declaring “user turn started” / “user turn ended.”
  - The authoritative turn state (`idle`, `listening`, `user_turn`, `assistant_speaking`, etc.).
- No other module may call ASR open/close directly or maintain independent `turn_open/armed/ready` flags that contradict the TurnEngine.
- Legacy fields like `accepting_audio`, `client_capture_armed`, `asr_ready`, `session.asr_state` must become derived state from the TurnEngine, not independent control levers.

### INV-3: Minimal Client Audio Gating

- The browser PCM sender may gate audio only on:
  - WebSocket connection state (connected vs. not).
  - Mic/VAD signal (speech vs. silence).
  - Local user mute/hold controls.
- The client **must not** gate audio on greet state, ASR open/ready, or server phase flags (`greet_completed`, `conversation_ready`, etc.).
- The client may emit control events such as `client.turn_start` when VAD first detects speech, but it does not decide whether ASR is open or whether a turn is valid.

### INV-4: Per-Turn Lifecycle Logging

- Every user turn must have a structured lifecycle record (log or DB row) capturing at least: `turn_id`, `first_audio_ts`, `asr_open_ts`, `asr_final_ts` (or timeout flag), `tts_start_ts`, `tts_end_ts`.
- Missing timestamps must make a “turn incomplete” condition detectable; this is a bug to fix, not normal behavior.
- Debugging should be possible by reading a single timeline per turn without inferring state from scattered flags.

### INV-5: Timer & Timeout Ownership

- Vendor ASR timeouts (e.g., “no audio for N seconds”) are normal end-of-turn conditions, not catastrophic errors.
- The server TurnEngine is the primary owner of turn timeouts and EOS behavior: it decides when a turn ends based on ASR final/timeout and/or server-side silence detection.
- Client-side timers (conversation watchdogs, partial watchdogs, “lazy timers,” nudges):
  - May drive UI and logging.
  - Must **not** directly open/close ASR, change server turn state, or gate PCM sending.
- UX timers (“I didn’t hear anything from your mic…”) are allowed but are not part of the core transport/ASR control plane.

## Design implications

- `_handle_binary` (or equivalent) should evolve into a mailbox that always buffers mic frames and never bounces them for state reasons.
- The TurnEngine should be the **only** place to see calls like `asr_open()` / `asr_close()` or flags like `turn_active`.
- Client audio-lane code that gates on greet state or ASR readiness is legacy and must be refactored to align with INV-3.
- “Fix it with a timer” in client code is discouraged; timing/timeout policies belong in the TurnEngine.

## Enforcement strategy

- Future work will add small tests such as:
  - Asserting no `audio_not_expected` error frames on the mic lane (INV-1).
  - Asserting exactly one ASR open/close per user turn (INV-2 & INV-4).
- Any PR touching the conversational core must be checked against these invariants. If an invariant must change, the engineer will update this document and the corresponding ADR and clearly justify the change.
