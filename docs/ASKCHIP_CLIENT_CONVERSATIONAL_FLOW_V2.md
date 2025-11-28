# AskChip Client Conversational Flow — v2

## Overview
The frontend orchestrates greet playback, mic/PCM/VAD gating, and WS interaction for AskChip. Behavior below reflects the current code paths only.

## State & Phase Model (Client)
- `PHASE` enum covers `boot → greet → conversation_ready → user_turn → closing → closed`; `createVoicePhaseController` drives transitions and logs changes.【F:app/static/js/voice/phase_controller.js†L1-L60】
- `markGreetStart` warms the AudioContext, tracks greet `utt_id`, sets `PHASE.Greet`, disables barge-in, pauses PCM, and stops mic capture.【F:app/static/js/ws_client.js†L472-L559】
- `markGreetEnd` moves to `ConversationReady`, then asynchronously reacquires mic hardware, ensures the audio graph, re-enables the mic track, flags mic/PCM readiness, and schedules the conversation start timer.【F:app/static/js/ws_client.js†L561-L588】
- `safeStartRecorderStreaming` is hard-gated to `ConversationReady/UserTurn`; all other phases log and return `false`.【F:app/static/js/ws_client.js†L701-L782】

## Lifecycle: Full Client Conversational Flow

### Boot / Page Load
- `app.js` initializes telemetry bridge, ensures `window.AppState`, and pre-sets `AppState.phase` to `"greet"` for gating compatibility. Audio modules load first so capture/ws runtimes are available to later dynamic imports.【F:app/static/js/app.js†L24-L199】

### User Start / Connect
- Mic hardware is warmed by `ensureMicHardware` when invoked (tracks Ready/Failed states, disables track until ready). Repeated calls reuse a live track; failures emit `client.mic.hardware_failed`.【F:app/static/js/audio/capture_runtime.js†L7-L112】
- The WS audio runtime queues PCM until `req_id` is set; `safeSendAudioChunk` drops sends when `phase === greet` or no active turn ID, logging `client.audio_chunk_dropped`.【F:app/static/js/audio/ws_audio_runtime.js†L494-L540】

### Greet Phase (Client)
- Greet start detection accepts `greet.*` frames or `tts.start` with `meta.is_greet`; `markGreetStart` warms audio, pauses PCM, disables barge-in, and stops recorder (`stopRecorderStreaming`/`autoStopRecorder`).【F:app/static/js/ws_client.js†L431-L559】
- During greet, PCM send is blocked by `safeSendAudioChunk` and capture acquisition checks `isGreetPhase` (gated in callers like `safeStartRecorderStreaming`).【F:app/static/js/audio/ws_audio_runtime.js†L494-L504】【F:app/static/js/ws_client.js†L701-L782】

### Greet → ConversationReady Transition (Client)
- `markGreetEnd` triggers mic reacquire: `ensureMicHardware()` → `ensureAudioGraph("greet_to_conversation_ready")` → `markMicAndPcmReady("audio_graph_live")` → mic track `enabled = true`; then `scheduleConversationStartAfterGreet` arms entry into conversation.【F:app/static/js/ws_client.js†L561-L588】

### Conversation Phase (Full Duplex)
- `enterConversationAfterGreet` commits when WS ready + ASR ready, forcing phase to `UserTurn` and syncing AppState; otherwise retries until ready.【F:app/static/js/ws_client.js†L784-L895】
- `safeRequestAsrOpen` only proceeds in conversation phases and forwards `requestAsrArm/openAsr` calls, logging intent/skip reasons.【F:app/static/js/ws_client.js†L641-L699】
- Mic start: `safeStartRecorderStreaming` enforces phase gate, then calls `ensureTurnAudioReqId` and `WSClient.startRecorderStreaming`; AppState.listening is toggled in runtime (via WSClient) once capture begins.【F:app/static/js/ws_client.js†L701-L782】
- PCM sender: `ws_audio_runtime` requires an active `req_id`; chunks include reqId/sampleRate metadata, and missing IDs cause drops to avoid orphan audio.【F:app/static/js/audio/ws_audio_runtime.js†L494-L540】
- VAD + barge-in: `canBargeIn` mirrors conversation phases. VAD event handling in capture_runtime (not shown here) drives `senderPaused` and auto-stop policies tied to AppState phase.

### ASR & Turn Handling
- `safeRequestAsrOpen` and phase checks gate ASR open; ASR readiness (`AppState.asrReady`) is awaited before committing `enterConversationAfterGreet`, ensuring turn capture opens post-greet.【F:app/static/js/ws_client.js†L641-L699】【F:app/static/js/ws_client.js†L784-L846】
- PCM flow requires `getCurrentTurnReqId`; drops otherwise, preventing ASR feed without server-issued turn IDs.【F:app/static/js/audio/ws_audio_runtime.js†L494-L538】

### Error Handling & Reacquire
- Mic failures mark hardware state `Failed` and log `client.mic.hardware_failed`; reconnection calls `ensureMicHardware` again, verifying live tracks and resetting state if tracks died.【F:app/static/js/audio/capture_runtime.js†L30-L112】

### Session End / Cleanup
- Recorder stop is invoked on greet start and other gating paths; PCM sender pauses via `setSenderPauseReason` and graph readiness flags gate further start attempts.【F:app/static/js/ws_client.js†L472-L559】

### Echo / Feedback Prevention (Client)
- `guard_mic_monitor.js` patches `AudioNode.connect` to trace mic-fed paths and block any connection from mic sources to audible destinations, logging `mic_guard.block` and emitting `client.mic_monitor_blocked`. It also propagates mic lineage across downstream nodes.【F:app/static/js/audio/guard_mic_monitor.js†L4-L254】
- Media elements assigned mic streams are auto-muted (`muted=true`, `volume=0`) to prevent local echo.【F:app/static/js/audio/guard_mic_monitor.js†L264-L329】

## Utopia Client Conversational Architecture
- Deterministic phases where greet fully gates mic/PCM/VAD/ASR; conversation entry only after explicit greet-end + ASR-ready handshake.
- Mic lifecycle: single warm-up, graph reuse across turns, deterministic unmute when conversation starts; no out-of-phase capture attempts.
- VAD/PCM gating: PCM only when `req_id` active and ASR open; VAD toggles senderPaused/barge-in with explicit policies; TTS cancel cleanly interrupts playback and re-arms capture.
- Echo control: mic sources can never reach `AudioDestinationNode`; only TTS/output nodes feed speakers.

## Gap Analysis: Client (As Implemented vs Utopia)
- **Match**: Phase enum and greet gating exist; PCM send blocked during greet; mic guard blocks feedback paths; mic reacquire after greet warms graph before conversation.【F:app/static/js/voice/phase_controller.js†L1-L60】【F:app/static/js/audio/ws_audio_runtime.js†L494-L504】【F:app/static/js/audio/guard_mic_monitor.js†L4-L254】【F:app/static/js/ws_client.js†L561-L588】
- **Partial**: Conversation commit retries on readiness but relies on timers; VAD/barge-in control referenced via `canBargeIn` without centralized VAD policy description here; ASR readiness depends on AppState flags external to this module.【F:app/static/js/ws_client.js†L630-L895】
- **Mismatch**: No explicit hard gate preventing warm-up recorder attempts beyond phase check; greet start relies on frame detection and may miss if metadata absent; detailed VAD-to-senderPaused mapping not consolidated for deterministic barge-in timing.
