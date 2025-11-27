# AskChip Conversational Flow — Current vs Target Design

Goal: document the present greet + full-duplex audio behavior and outline the target design so greet playback stays isolated from mic/PCM/ASR/VAD until conversation begins. Section 2 summarizes today’s code-backed flow; Section 3 proposes the recommended behavior and the code points to adjust.

## 2. Current Behavior — Detailed, Code-backed Flow

### 2.1 Phase Model & State Machines
- `PHASE` values are defined in `voice/phase_controller.js` with the initial phase set to `Boot`, plus `Greet`, `ConversationReady`, `UserTurn`, `Closing`, and `Closed` transitions via helper methods (e.g., `markGreetStart`, `markGreetEnd`, `enterConversation`).【F:app/static/js/voice/phase_controller.js†L1-L60】
- `ws_client.js` instantiates `voicePhaseController` and exposes helpers like `getPhase`/`isGreetPhase`, wiring the controller onto `window` for debugging.【F:app/static/js/ws_client.js†L97-L123】
- Phase transitions during greet/conversation include `markGreetStart`, `markGreetEnd`, `enterConversation`, and `endUserTurn`, with telemetry logging and AppState synchronization for each change.【F:app/static/js/ws_client.js†L425-L520】【F:app/static/js/ws_client.js†L673-L770】【F:app/static/js/ws_client.js†L2532-L2543】

### 2.2 Greet Flow (Handshake → Greet TTS)
- Greet start is detected from frames such as `greet`, `greet.start`, `greet.begin`, or `tts.start` marked `meta.is_greet`, triggering `markGreetStart` to set the phase to `Greet`, warm the audio context, disable barge-in, pause the PCM sender, and stop the mic if active.【F:app/static/js/ws_client.js†L425-L520】
- Greet end is detected from `greet.end`, `greet.complete`, or `tts.end` while in `Greet`, calling `markGreetEnd` and scheduling conversation start once greet completes.【F:app/static/js/ws_client.js†L436-L465】【F:app/static/js/ws_client.js†L2355-L2364】
- During the initial WS info gate, incoming frames are queued until an `info` frame arrives; greet-related info logging occurs via `phase.greet.expectInfo/receivedInfo` before handling the `info` frame and promoting the WS phase to `ready`.【F:app/static/js/ws_client.js†L2366-L2406】
- `handleInfoFrame` applies any greet TTS descriptor (`meta.tts_audio` or `frame.audio`) to `frameParser` or `AudioPlayer` before updating connection state and policy snapshot.【F:app/static/js/ws_client.js†L3365-L3411】
- `tts.start` frames set `ttsActive`, optionally warm output for greet, and delegate to `audioPlayer.handleTtsStart`, while keeping PCM paused for TTS.【F:app/static/js/ws_client.js†L2637-L2658】

### 2.3 Post-Greet Flow — Full Duplex Conversation
- Conversation start is scheduled after greet via `scheduleConversationStartAfterGreet`, which waits for audio readiness, delays by `CONVERSATION_START_DELAY_MS`, then calls `enterConversationAfterGreet`.【F:app/static/js/ws_client.js†L792-L823】
- `enterConversationAfterGreet` transitions to `UserTurn`, requests ASR (`safeRequestAsrOpen`), starts the recorder (`safeStartRecorderStreaming`), clears greet pause reasons, and re-enables barge-in when conditions are met.【F:app/static/js/ws_client.js†L673-L770】
- ASR open is gated to `ConversationReady`/`UserTurn` phases in `safeRequestAsrOpen`; otherwise it logs a skip.【F:app/static/js/ws_client.js†L563-L621】
- Recorder start attempts respect greet blocking, but also log “out of phase” starts when invoked before conversation readiness, meaning warm-up can occur even outside conversation phases.【F:app/static/js/ws_client.js†L623-L671】【F:app/static/js/ws_client.js†L1722-L1806】

### 2.4 Current Server→Client Audio Paths
- `frame_parser.handleRawMessageData` decodes JSON/msgpack or raw binary frames, opening a TTS gate on `tts.start`, closing on `tts.end/cancel/error`, and enqueuing binary audio chunks to `AudioPlayer.enqueueChunk` when the gate is open.【F:app/static/js/ws/frame_parser.js†L352-L413】
- Greet and conversation TTS descriptors are applied via `frameParser.setTtsAudioDescriptor` (or `AudioPlayer.setDescriptor` fallback) when processing the `info` frame.【F:app/static/js/ws_client.js†L3365-L3411】
- `ws_client` handles `tts.start/tts.end` for UI state and logging but delegates actual chunk playback to the shared `frame_parser` pipeline; no separate `audio.chunk` handler exists in `ws_client` today.【F:app/static/js/ws_client.js†L2637-L2699】【F:app/static/js/ws/frame_parser.js†L367-L451】

### 2.5 Current Mic+VAD+PCM Behavior Around Greet
- `safeStartRecorderStreaming` and `WSClient.startRecorderStreaming` both block when `phase === Greet`, but the latter still allows starts outside `ConversationReady/UserTurn`, logging them as “out of phase.”【F:app/static/js/ws_client.js†L623-L671】【F:app/static/js/ws_client.js†L1722-L1806】
- Auto stop logic suppresses recorder stops during greet unless forced, ensuring greet isn’t interrupted by VAD stop signals.【F:app/static/js/ws_client.js†L1709-L1719】【F:app/static/js/ws_client.js†L835-L841】
- `startCaptureFromPolicy` in `capture_runtime` refuses to acquire the mic during greet based on the global voice phase check.【F:app/static/js/audio/capture_runtime.js†L716-L729】
- PCM sending is gated in `ws_audio_runtime.safeSendAudioChunk`, which drops audio when the app phase is `Greet`.【F:app/static/js/audio/ws_audio_runtime.js†L427-L437】

## 3. Target / Recommended Design — With Mapping to Code

### 3.1 Target Phase & Flow Model
- **PHASE.Greet** (handshake + greet playback): Chip may speak; mic/ASR/VAD/PCM are off and non-controlling. Transition in via greet start detection; transition out via explicit greet end or TTS completion. Map to `voicePhaseController.markGreetStart/markGreetEnd` and uses in `ws_client` (e.g., `frameSignalsGreetStart`/`frameSignalsGreetEnd`).【F:app/static/js/voice/phase_controller.js†L31-L39】【F:app/static/js/ws_client.js†L425-L465】
- **PHASE.ConversationReady** (post-greet idle) → **PHASE.UserTurn/AssistantTurn** (active turns) → **PHASE.Closing/Closed** as today. Conversation-ready entry should happen immediately after greet completion; later transitions (turn begin/end, closing) already use `voicePhaseController` hooks in `ws_client`.【F:app/static/js/voice/phase_controller.js†L36-L59】【F:app/static/js/ws_client.js†L2532-L2543】
- Guard rails: Mic/ASR/PCM/VAD should only activate when `ConversationReady` or `UserTurn`—never during `Greet` or `Boot`.

### 3.2 Single Audio Pipeline (Server→Client)
- Desired path: `ws/connection.js → ws/frame_parser.handleRawMessageData → AudioPlayer.enqueueChunk`, driven by TTS gate state from `frame_parser.handleTtsGateFrame`. Both greet and conversation TTS use this path; the phase only controls gating logic, not a separate handler.【F:app/static/js/ws/frame_parser.js†L352-L451】
- Code alignment: keep descriptor application centralized (`handleInfoFrame` → `frameParser.setTtsAudioDescriptor`), and ensure no direct `audio.chunk` playback occurs outside `frame_parser` (currently true in `ws_client` where TTS handling is UI/log only).【F:app/static/js/ws_client.js†L3365-L3411】【F:app/static/js/ws_client.js†L2637-L2699】

### 3.3 Mic / PCM / ASR / VAD Rules
- Mic may start only in `ConversationReady` or `UserTurn`; VAD must not auto-start/stop during `Greet`; PCM sender must not transmit during greet.
- Mapping to current code:
  - `safeStartRecorderStreaming` already blocks greet but should hard-return false when not in conversation-ready phases instead of warming up out-of-phase.【F:app/static/js/ws_client.js†L623-L671】【F:app/static/js/ws_client.js†L1722-L1806】
  - `startCaptureFromPolicy` and `safeSendAudioChunk` already check for greet; reinforce phase checks so PCM/VAD respect the conversation-only rule.【F:app/static/js/audio/capture_runtime.js†L716-L729】【F:app/static/js/audio/ws_audio_runtime.js†L427-L437】
  - `safeRequestAsrOpen` should remain phase-guarded; ensure greet completion transitions to `ConversationReady` before any ASR arm attempts.【F:app/static/js/ws_client.js†L563-L621】【F:app/static/js/ws_client.js†L673-L770】

### 3.4 Concrete Recommended Code Changes (Do Not Implement Yet)
- **[Phase Model]** In `voice/phase_controller.js` and `ws_client` transitions, ensure the default path is `Boot → Greet → ConversationReady`, with greet end driven by explicit signals or `tts.end` while in greet; keep `enterConversation` reserved for post-greet. References: `markGreetStart/markGreetEnd` and greet signal detection in `ws_client`.【F:app/static/js/voice/phase_controller.js†L31-L39】【F:app/static/js/ws_client.js†L425-L465】
- **[Mic Gating]** Update `safeStartRecorderStreaming` and `WSClient.startRecorderStreaming` to return `false` unless the phase is `ConversationReady` or `UserTurn`; remove/avoid warm-up starts logged as “out of phase.” References: existing phase checks and out-of-phase logging blocks.【F:app/static/js/ws_client.js†L623-L671】【F:app/static/js/ws_client.js†L1722-L1806】
- **[Server→Client Audio Unification]** Keep playback centralized in `frame_parser`; audit/remove any future direct `audio.chunk` handlers in `ws_client`, ensuring greet TTS also flows through the gate established by `handleTtsGateFrame`. References: `frame_parser.handleRawBinaryFrame/handleTtsGateFrame` and `ws_client` TTS handling for UI only.【F:app/static/js/ws/frame_parser.js†L352-L413】【F:app/static/js/ws_client.js†L2637-L2699】
- **[Greet Protection for Mic/PCM]** Enforce greet-phase guards in mic/PCM paths: `capture_runtime.startCaptureFromPolicy`, `ws_audio_runtime.safeSendAudioChunk`, and recorder auto-stop logic should all reject greet-phase operations so no mic/VAD/PCM runs until greet ends. References: current greet blocks in those functions.【F:app/static/js/audio/capture_runtime.js†L716-L729】【F:app/static/js/audio/ws_audio_runtime.js†L427-L437】【F:app/static/js/ws_client.js†L1709-L1719】
- **[Conversation Entry Trigger]** Simplify post-greet entry by making `markGreetEnd` immediately set phase to `ConversationReady` and trigger ASR/mic start only after greet completion (e.g., via `scheduleConversationStartAfterGreet`). References: greet end handling and post-greet scheduling logic.【F:app/static/js/ws_client.js†L436-L465】【F:app/static/js/ws_client.js†L792-L823】

