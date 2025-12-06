# ADR 0001: Conversation Core Constitution

## Status

Accepted

## Context

AskChip’s conversational core (audio transport, ASR lifecycle, turn engine) has accumulated ad-hoc rules that are difficult to reason about. We need explicit, stable invariants to govern future changes without altering runtime behavior today.

## Decision

Adopt a Conversation Core Constitution that codifies five hard invariants (INV-1 through INV-5):

1. **INV-1 Mailbox / Always-Buffer Rule:** The WebSocket mic lane always accepts binary audio and pushes it into a bounded buffer, updating ingress metrics and dropping oldest audio on overflow. No `audio_not_expected`-style rejections occur at ingest; “is this usable?” decisions happen after buffering.
2. **INV-2 Single Source of Turn Truth:** A single server component (TurnEngine) owns ASR open/close, authoritative turn state, and user-turn start/end declarations. Other modules must not manage competing ASR or turn flags; legacy fields become derived state.
3. **INV-3 Minimal Client Audio Gating:** The browser gates PCM only on WS connectivity, VAD/mic activity, and local mute/hold. It does not gate on greet or ASR readiness and may only emit advisory control events such as `client.turn_start`.
4. **INV-4 Per-Turn Lifecycle Logging:** Every user turn records a structured timeline (`turn_id`, first audio, ASR open/final/timeout, TTS start/end). Missing timestamps should surface as “turn incomplete” issues.
5. **INV-5 Timer & Timeout Ownership:** TurnEngine owns turn timeouts/EOS. Vendor ASR timeouts end turns normally. Client timers are limited to UI/logging and cannot control ASR or gate PCM.

## Consequences

- WebSocket adapter follows a mailbox pattern: always buffer mic frames, log overflow, never bounce frames for state reasons.
- TurnEngine is the single locus for ASR open/close and authoritative turn state; related flags elsewhere become derived.
- Client audio gating is simplified to connectivity, VAD, and local mute/hold only; greet/ASR gating is legacy to be removed.
- Timer ownership sits on the server; client timers remain UI/UX aids only.
- Future ADRs and PRs that touch the conversational core must reference these invariants and update them explicitly if they need to change.
