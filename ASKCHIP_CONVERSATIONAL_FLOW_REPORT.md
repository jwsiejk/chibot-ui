# AskChip Conversational Flow — Current vs Target Design

Goal: document the present greet + full-duplex audio behavior and outline the target design so greet playback stays isolated from mic/PCM/ASR/VAD until conversation begins. Section 2 summarizes today’s code-backed flow; Section 3 proposes the recommended behavior and the code points to adjust.

## 2. Current Behavior — Detailed, Code-backed Flow

### 2.1 Phase Model & State Machines
- `PHASE` is an enum with `Boot`, `Greet`, `ConversationReady`, `UserTurn`, `Closing`, `Closed`; the initial value is `Boot`. Transitions are implemented as methods on the controller: `markGreetStart` → `Greet`, `markGreetEnd` → `ConversationReady`, `enterConversation` → `UserTurn`, `endUserTurn` → `ConversationReady`, `beginClosing` → `Closing`, `markClosed` → `Closed`.【F:app/static/js/voice/phase_controller.js†L1-L61】
- `ws_client.js` constructs `voicePhaseController`, exposes helpers (`getPhase`, `isGreetPhase`), and syncs phase into `AppState` for logging/telemetry (e.g., `syncAppStatePhase`).【F:app/static/js/ws_client.js†L97-L123】【F:app/static/js/ws_client.js†L493-L545】
- Greet transitions: `frameSignalsGreetStart` detects `greet/greet.start/greet.begin` or `tts.start` with `meta.is_greet`, then `markGreetStart` sets `PHASE.Greet`, warms audio, disables barge-in, pauses PCM, and stops mic.【F:app/static/js/ws_client.js†L425-L537】
- Greet end: `frameSignalsGreetEnd` accepts `greet.end/greet.complete` or `tts.end` while in `Greet`; `markGreetEnd` sets `ConversationReady` and syncs `AppState` (but no additional side effects).【F:app/static/js/ws_client.js†L436-L546】
- Conversation entry after greet uses `scheduleConversationStartAfterGreet` (timer) → `enterConversationAfterGreet` (sets `UserTurn`, opens ASR, starts recorder, re-enables PCM/barge-in).【F:app/static/js/ws_client.js†L673-L823】
- Reality check: production logs occasionally show `AppState.phase === "boot"` during greet, implying some greet-start paths are not firing `markGreetStart`/`syncAppStatePhase` consistently.

### 2.2 Greet Flow (Handshake → Greet TTS)
- `phase.greet.expectInfo/receivedInfo` logs: emitted while the info gate is active before the `info` frame is processed; `expectInfoFrame` guards, logging `phase.greet.expectInfo` for queued frames and `phase.greet.receivedInfo` once the `info` frame arrives, before calling `handleInfoFrame`.【F:app/static/js/ws_client.js†L2366-L2406】
- `handleInfoFrame` attaches greet TTS descriptors: extracts `meta.tts_audio` or `frame.audio`, logs `client.ws.tts_descriptor_frame`, then calls `frameParser.setTtsAudioDescriptor` (fallback to `AudioPlayer.setDescriptor`) before updating connection/policy state.【F:app/static/js/ws_client.js†L3365-L3411】
- `frameSignalsGreetStart`/`markGreetStart` path: greet start triggers when a frame is `greet/greet.start/greet.begin` or `tts.start` with `meta.is_greet`; `markGreetStart` sets the phase, warms the audio context, disables barge-in, pauses PCM, and stops the mic.【F:app/static/js/ws_client.js†L425-L537】
- `frameSignalsGreetEnd`/`markGreetEnd` path: greet end triggers on `greet.end/greet.complete` or `tts.end` while phase is `Greet` (with metadata fallback); `markGreetEnd` sets `ConversationReady` but does not itself start ASR/mic.【F:app/static/js/ws_client.js†L436-L546】【F:app/static/js/ws_client.js†L2355-L2364】
- Greet TTS handling: greet playback is treated like normal TTS; `tts.start` sets `ttsActive`, can warm output when `meta.is_greet`, and delegates to `audioPlayer.handleTtsStart`; `tts.end` clears flags and invokes `audioPlayer.handleTtsEnd`. There is no separate greet audio path.【F:app/static/js/ws_client.js†L2626-L2705】
- Reality check: in production logs we sometimes see greet descriptor logs and TTS playback while `AppState.phase` remains `boot`, indicating greet start hooks are not reliably setting `PHASE.Greet`.

### 2.3 Post-Greet Flow — Full Duplex Conversation
Flow (textual):
`WS open → info (greet descriptor) → greet TTS → greet complete → scheduleConversationStartAfterGreet → enterConversationAfterGreet → safeRequestAsrOpen → safeStartRecorderStreaming → full duplex`
- `scheduleConversationStartAfterGreet` waits for audio readiness, applies `CONVERSATION_START_DELAY_MS`, and then calls `enterConversationAfterGreet`.【F:app/static/js/ws_client.js†L792-L823】
- `enterConversationAfterGreet` sets phase to `UserTurn`, requests ASR (`safeRequestAsrOpen`), starts recorder (`safeStartRecorderStreaming`), clears greet pause reasons, and re-enables barge-in/PCM.【F:app/static/js/ws_client.js†L673-L790】
- `safeRequestAsrOpen` only proceeds in `ConversationReady/UserTurn`, otherwise logs a skipped open.【F:app/static/js/ws_client.js†L563-L621】
- `safeStartRecorderStreaming` is invoked immediately after ASR open; greet-phase starts are blocked, but non-conversation phases fall through as “warm-up” (out-of-phase) attempts.【F:app/static/js/ws_client.js†L623-L671】【F:app/static/js/ws_client.js†L727-L738】

### 2.4 Current Server→Client Audio Paths
- Centralized parsing: `frame_parser.handleRawMessageData` normalizes frames, opens/closes a TTS gate on `tts.start`/`tts.end`, and routes binary audio through `handleRawBinaryFrame` to `AudioPlayer.enqueueChunk` (dropping if the gate is closed).【F:app/static/js/ws/frame_parser.js†L330-L460】
- Greet TTS descriptor path: the `info` frame applies descriptors via `frameParser.setTtsAudioDescriptor` (fallback `AudioPlayer.setDescriptor`) before state updates.【F:app/static/js/ws_client.js†L3365-L3411】
- No direct `audio.chunk` playback in `ws_client`: TTS handling in `ws_client` updates UI/telemetry, while audio chunks are consumed by `frame_parser`; there are no `AudioPlayer.enqueueChunk` calls in `ws_client`.【F:app/static/js/ws_client.js†L2626-L2699】【F:app/static/js/ws/frame_parser.js†L352-L413】
- Audio pipelines today:
  - **Greet TTS**: WS `info` frame with `meta.tts_audio` → `handleInfoFrame` → `frameParser.setTtsAudioDescriptor` → `AudioPlayer` (descriptor) — greet audio chunks still go through `frame_parser.handleRawMessageData` → `handleRawBinaryFrame` → `AudioPlayer.enqueueChunk`.【F:app/static/js/ws_client.js†L3365-L3411】【F:app/static/js/ws/frame_parser.js†L330-L413】
  - **Conversation TTS**: WS `tts.start` → `frame_parser.handleTtsGateFrame` (open); WS binary audio → `handleRawBinaryFrame` → `AudioPlayer.enqueueChunk`; WS `tts.end` → `handleTtsGateFrame` (close).【F:app/static/js/ws/frame_parser.js†L330-L413】

### 2.5 Current Mic+VAD+PCM Behavior Around Greet
- `safeStartRecorderStreaming` blocks when `phase === Greet` but otherwise allows “warming up” starts outside `ConversationReady/UserTurn`, logging them as out-of-phase; `WSClient.startRecorderStreaming` repeats the greet block but still attempts warm-up when preconditions aren’t met.【F:app/static/js/ws_client.js†L623-L671】【F:app/static/js/ws_client.js†L1722-L1806】
- Auto-stop suppression: `autoStopRecorder` ignores VAD-driven stops during greet unless forced.【F:app/static/js/ws_client.js†L1709-L1719】
- Mic acquisition guard: `capture_runtime.startCaptureFromPolicy` refuses mic acquisition during greet via `isGreetPhaseSafe` (phase check).【F:app/static/js/audio/capture_runtime.js†L716-L729】
- PCM send guard: `ws_audio_runtime.safeSendAudioChunk` drops audio when `AppState.phase === Greet`.【F:app/static/js/audio/ws_audio_runtime.js†L427-L437】
- Summary: mic/PCM/VAD are partially gated by greet across capture and PCM senders, but `safeStartRecorderStreaming` still allows out-of-phase starts, leading to early mic warm-up attempts before conversation readiness.

## 3. Target / Recommended Design — With Mapping to Code

### 3.1 Target Phase & Flow Model
Current (high level): `Boot → (handshake + greet) → Boot (observed) → ConversationReady/UserTurn` where mic warm-up may occur early.

Target: `Boot → Greet → ConversationReady → UserTurn/AssistantTurn → Closing/Closed` with **no mic/PCM/VAD/ASR during Greet**.

- **PHASE.Greet**: handshake + greet playback; phase authority for gating. Transition in via `frameSignalsGreetStart` → `markGreetStart`; transition out via `frameSignalsGreetEnd`/`tts.end` → `markGreetEnd`.【F:app/static/js/voice/phase_controller.js†L31-L39】【F:app/static/js/ws_client.js†L425-L546】
- **PHASE.ConversationReady → PHASE.UserTurn/AssistantTurn → Closing/Closed**: same as today, but entry to `ConversationReady` must immediately follow greet end so mic/ASR gating aligns with real state.【F:app/static/js/voice/phase_controller.js†L36-L59】【F:app/static/js/ws_client.js†L673-L790】
- Core correction: ensure `PHASE.Greet` is active throughout Chip’s greeting in both controller and `AppState`, making phase the authority for mic/PCM/VAD gating.

### 3.2 Single Audio Pipeline (Server→Client)
- All server→client audio that plays must flow through `frame_parser.handleRawMessageData` → `AudioPlayer.*`; TTS gate opens on `tts.start` and closes on `tts.end/cancel/error` to control `AudioPlayer.enqueueChunk`.【F:app/static/js/ws/frame_parser.js†L330-L413】
- Greet and conversation TTS share this pipeline: greet descriptors arrive via `info` → `frameParser.setTtsAudioDescriptor`, and greet audio chunks still enter through `frame_parser`/`AudioPlayer` (no direct WS handlers).【F:app/static/js/ws_client.js†L3365-L3411】【F:app/static/js/ws/frame_parser.js†L330-L413】
- Any future audio frame types must be routed into `frame_parser`; avoid direct `audio.chunk → AudioPlayer.enqueueChunk` hooks in `ws_client` or other modules.

### 3.3 Mic / PCM / ASR / VAD Rules
- No mic acquisition, PCM streaming, ASR input, or VAD auto-control may occur while `phase === PHASE.Greet` or any pre-conversation value (e.g., `Boot`).
- Function-level enforcement:
  - `safeStartRecorderStreaming` (ws_client.js): hard gate; return false outside `ConversationReady/UserTurn` (no warm-up starts).【F:app/static/js/ws_client.js†L623-L671】
  - `startCaptureFromPolicy` (capture_runtime.js): refuse mic when `isGreetPhaseSafe()` is true.【F:app/static/js/audio/capture_runtime.js†L716-L729】
  - `safeSendAudioChunk` (ws_audio_runtime.js): drop PCM sends when `AppState.phase === Greet`.【F:app/static/js/audio/ws_audio_runtime.js†L427-L437】
  - `safeRequestAsrOpen` (ws_client.js): only arm ASR in `ConversationReady/UserTurn`.【F:app/static/js/ws_client.js†L563-L621】

### 3.4 Concrete Recommended Code Changes (Do Not Implement Yet)
- **[Phase Model]**
  - Step 1: Ensure every greet start signal calls `markGreetStart` and `syncAppStatePhase` so `phase === Greet` during playback (e.g., consolidate `frameSignalsGreetStart` handling paths).【F:app/static/js/ws_client.js†L425-L546】
  - Step 2: Ensure greet end always calls `markGreetEnd` then immediately transitions toward conversation start (schedule timer or direct enter).【F:app/static/js/ws_client.js†L436-L546】【F:app/static/js/ws_client.js†L792-L823】
  - Step 3: Add temporary logging to confirm `phase: 'greet'` spans the entire greet playback.
- **[Mic Gating]**
  - Step 1: In `safeStartRecorderStreaming`, replace warm-up behavior with a hard gate: return `false` in all non-`ConversationReady/UserTurn` phases.【F:app/static/js/ws_client.js†L623-L671】
  - Step 2: Align `WSClient.startRecorderStreaming` messaging to avoid out-of-phase starts and rely on the same gating logic.【F:app/static/js/ws_client.js†L1722-L1806】
  - Step 3: Verify logs show no `client.audio_chunk_send` during greet to confirm gating.
- **[Server→Client Audio Unification]**
  - Step 1: Keep all audio chunk handling inside `frame_parser`; prevent any new direct `AudioPlayer.enqueueChunk` usages in `ws_client` or elsewhere.【F:app/static/js/ws/frame_parser.js†L330-L413】
  - Step 2: If greet remains special-cased, ensure it still uses the TTS gate (open on greet start, close on greet end/tts.end) but flows through the same `AudioPlayer` path.【F:app/static/js/ws_client.js†L2626-L2699】
- **[Greet Protection for Mic/PCM]**
  - Step 1: Reinforce greet-phase guards in `capture_runtime.startCaptureFromPolicy` and `ws_audio_runtime.safeSendAudioChunk` so mic/PCM refuse greet-phase operations without exceptions.【F:app/static/js/audio/capture_runtime.js†L716-L729】【F:app/static/js/audio/ws_audio_runtime.js†L427-L437】
  - Step 2: Keep `autoStopRecorder` suppression explicit for greet to avoid VAD-induced stops during playback.【F:app/static/js/ws_client.js†L1709-L1719】
- **[Conversation Entry Trigger]**
  - Step 1: After `markGreetEnd`, immediately schedule or perform `enterConversationAfterGreet` to reach `ConversationReady` and arm ASR/mic only after greet completion.【F:app/static/js/ws_client.js†L436-L546】【F:app/static/js/ws_client.js†L673-L823】
  - Step 2: Confirm through telemetry that ASR/mic start events only occur after the greet end log and phase transition.

## 4. Gaps & Mismatches Between Code and Intended Design

- **Phase mismatch**: Intended `PHASE.Greet` during greet, but logs sometimes show `AppState.phase === "boot"` during greet, implying missing `markGreetStart`/`syncAppStatePhase` in some paths.
- **Mic warm-up behavior**: Intended mic/PCM only after greet; current `safeStartRecorderStreaming` allows out-of-phase starts (warm-up) before conversation phases.【F:app/static/js/ws_client.js†L623-L671】【F:app/static/js/ws_client.js†L1722-L1806】
- **Logging/telemetry separation**: Greet info logs (`phase.greet.expectInfo/receivedInfo`) are separate from primary phase logs, complicating correlation of greet vs. conversation phases.【F:app/static/js/ws_client.js†L2366-L2406】

## 5. Implementation Plan Overview (Future Work)

1. Align phase transitions with greet reality: ensure greet start/end reliably set `PHASE.Greet → ConversationReady` and verify via telemetry.
2. Harden mic/PCM/ASR gating: enforce no mic/PCM/VAD/ASR while in greet or pre-conversation phases via `ws_client`, `capture_runtime`, and `ws_audio_runtime` gates.【F:app/static/js/ws_client.js†L563-L671】【F:app/static/js/audio/capture_runtime.js†L716-L729】【F:app/static/js/audio/ws_audio_runtime.js†L427-L437】
3. Unify audio playback: confirm all server→client audio (greet + conversation) flows through `frame_parser` → `AudioPlayer` with TTS gate control.【F:app/static/js/ws/frame_parser.js†L330-L413】
4. Validate in logs: add temporary instrumentation to prove greet holds `phase === Greet`, and no mic/PCM/ASR events fire until post-greet conversation start.

