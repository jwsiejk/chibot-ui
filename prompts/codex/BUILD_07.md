# BUILD 07 — Client v2 Minimal

**Alignment guard (do not omit):**
- This build MUST align with the SSOT docs in `/docs` and all previous builds.
- Do not touch files outside the listed scope.
- Do not rename routes, env vars, or policy keys.
- Every new/changed file ≤ 500 lines. Keep changes small and isolated.
- Preserve the `chat.v2` contract and include the telemetry block in policy frames.

## Tasks
### C7-A: WS + PolicyBus (stickiness)
**Files:** static/v2/runtime/ws.js, static/v2/policy/InteractionPolicy.js
**Non-goals:** No legacy client usage.
**Acceptance:**
- Applies `policy.interaction`; if `acwr` omitted (safety test), carry forward last value.

### C7-B: Waveform + states
**Files:** static/v2/ui/waveform.js, static/v2/ui/stateBadges.js
**Non-goals:** No avatar/visemes.
**Acceptance:**
- States reflect `tts.start`/`tts.end` and policy in real time.

### C7-C: Playback truth + auto barge
**Files:** static/v2/audio/player.js, static/v2/runtime/telemetry.js
**Non-goals:** No PTT.
**Acceptance:**
- onplay/onended emit client `tts_start/tts_end`; honors `barge_in_enabled`.

### C7-D: Recorder + sender
**Files:** static/v2/audio/recorder.js, static/v2/runtime/send.js
**Non-goals:** No echo canceller changes.
**Acceptance:**
- Server receives audio; `asr.partial`/`asr.final` round-trip.

