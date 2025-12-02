# AskChip Architecture – Step 1: Current State (Code-Driven)

## 1. High-Level Overview

### 1.1 What AskChip Is Today (per this repo)
- Single-page web client served by a Python ASGI app that exposes HTTP auth/admin routes and a `/ws/v2/chat` WebSocket for streaming voice chat. 【F:app/asgi_gateway.py†L100-L176】
- Voice assistant pipeline uses PCM16 mono audio over WebSocket, integrates streaming ASR (GCP), LLM-based NLU/NLG, policy-driven turn handling, and TTS output streamed back to clients. 【F:app/ws/adapter.py†L1-L175】【F:app/voice_v2/engine.py†L90-L174】
- Admin/debug utilities for logs, flow traces, and exported sessions, with client log aggregation. 【F:app/asgi_gateway.py†L312-L365】【F:app/asgi_gateway.py†L140-L176】

### 1.2 Major Components and Technologies
- Server: Python ASGI (custom gateway), WebSocket handling via uvicorn protocols, policy/LLM/ASR/TTS modules. 【F:app/asgi_gateway.py†L45-L66】【F:app/ws/adapter.py†L94-L133】
- Client: JavaScript modules loaded dynamically (`app.js` bootstraps), Web Audio for capture/playback, VAD, WS client, telemetry/logging. 【F:app/static/js/app.js†L45-L196】【F:app/static/js/audio/ws_audio_runtime.js†L1-L37】
- External services referenced: Google Cloud STT (GCP), OpenAI/LLM adapters, TTS provider defaults (voice id alloy). 【F:app/config.py†L69-L144】【F:app/voice_v2/engine.py†L114-L138】

## 2. Runtime & Deployment Today

### 2.1 Runtime Model
- ASGI entrypoint `app.asgi_gateway:app` dispatches lifespan, HTTP, and WebSocket scopes; WebSocket routed to `ChatV2Adapter`. 【F:app/asgi_gateway.py†L188-L235】
- Logging configured at import; telemetry bus installed. 【F:app/asgi_gateway.py†L45-L66】
- HTTP handlers for health/live/ready/info, auth, admin assets, static files; `/ws/v2/chat` drives voice path. 【F:app/asgi_gateway.py†L100-L235】

### 2.2 Environment & Configuration
- `.env` loaded if present; helpers for env bool/int/float. 【F:app/config.py†L14-L67】
- Key envs: ASR sample rate/language (`GCP_STT_DEFAULT_SAMPLE_RATE`, `GCP_STT_DEFAULT_LANGUAGE`), input gain, ASR backpressure thresholds, feature flags (AEC, ASR trace). 【F:app/config.py†L35-L47】【F:app/config.py†L69-L139】
- Policy defaults include media capture (PCM16 @16k mono), capture gating, ASR behavior (max utterance, silence commit, keep-warm), audio pipeline flags. 【F:app/config.py†L101-L174】
- WS adapter also reads feature flags (`FEATURE_LEGACY_POLICY`, `ALLOW_AUDIO_WITHOUT_ASR`) and ping/heartbeat timings. 【F:app/ws/adapter.py†L101-L150】【F:app/ws/adapter.py†L127-L150】

### 2.3 Deployment Assumptions
- No Dockerfile/Procfile shown; ASGI gateway assumes uvicorn-compatible server with WebSocket support; logging tuning references uvicorn loggers. 【F:app/asgi_gateway.py†L45-L66】
- Export directory `exports/` used for session logs and readiness probe. 【F:app/asgi_gateway.py†L125-L170】【F:app/asgi_gateway.py†L258-L276】
- Static assets served from `app/static` with manifest-based versioning. 【F:app/asgi_gateway.py†L19-L41】【F:app/asgi_gateway.py†L300-L365】

## 3. Server-Side Architecture (Current)

### 3.1 HTTP & WebSocket Surfaces
- HTTP routes: health (`/api/v1/health`, `/healthz`), liveness (`/api/v1/live`), readiness (`/api/v1/ready`), info/version, auth (`/api/v1/auth/login`, `/api/v1/auth/me`, `/api/v1/auth/profile`, `/api/v1/auth/ws-token`), admin settings/config/log exports and flow tracing, static root `/`. 【F:app/asgi_gateway.py†L100-L235】【F:app/asgi_gateway.py†L300-L365】
- WebSocket endpoint `/ws/v2/chat` with subprotocol `chat.v2` (also supports msgpack). Adapter performs optional JWT verification, policy normalization, rate limiting, and routes audio/JSON frames. 【F:app/asgi_gateway.py†L100-L210】【F:app/ws/adapter.py†L101-L200】
- Message schema enforced via adapter: outbound allowed types include policy info, TTS/ASR events, chat deltas, turn begin/end; inbound exempt types for telemetry/ping. 【F:app/ws/adapter.py†L150-L185】

### 3.2 Voice/ASR/TTS Engine (Current Implementation)
- EngineV2 maintains per-session `_TurnSession` state machine with states Ready→Listening→Thinking→Responding→ConfirmingBarge; tracks turn ids, req ids, TTS masks, metrics. 【F:app/voice_v2/engine.py†L114-L174】【F:app/voice_v2/engine.py†L190-L238】
- Components: GateController (gating events), VADAggregator, StreamingController, ConversationBuffer, NLUAdapter, PolicyDecider, LLMAdapter; subscribes to telemetry bus for chat and WS send events. 【F:app/voice_v2/engine.py†L207-L243】
- Default audio descriptor PCM16/16k mono, default voice `alloy-en-US-001`, locale `en-US`. 【F:app/voice_v2/engine.py†L114-L138】
- TTS runtime initialized in ASGI gateway with exporter; voice generation handled via TTSRuntime/tts_provider (not detailed here). 【F:app/asgi_gateway.py†L39-L43】
- Gating around greet/conversation states managed via GateController; turn planning uses planner/persona to build actions and suggestions. 【F:app/voice_v2/engine.py†L207-L243】

### 3.3 Audio Bridge & ASR Lifecycle
- WS adapter receives PCM frames, validates headers against policy (`validate_audio_header_against_policy`) and per-frame size (`validate_frame`); PCM expected 16-bit mono at 16k. 【F:app/ws/adapter.py†L89-L150】
- Audio throttling/backpressure thresholds (`QUEUE_ON_THRESHOLD`, `_AUDIO_THROTTLE_HINT_MS`) regulate queuing; telemetry events track audio summaries and vendor bytes. 【F:app/ws/adapter.py†L115-L125】【F:app/ws/adapter.py†L51-L67】
- ASR engine uses GCP streaming; constants enforce single stream, open/close events, keepalive ping intervals; adapter tracks ASR readiness and mic-open timeouts. 【F:app/ws/adapter.py†L51-L70】【F:app/ws/adapter.py†L145-L150】
- Keepalive audio chunks (20ms) and idle timers protect from silence timeouts; idle safety nets defined. 【F:app/ws/adapter.py†L113-L150】

### 3.4 Logging, Telemetry, and Admin/Debug Facilities
- Telemetry bus installed globally; `FileExporter` writes NDJSON session logs under `exports/<sid>/logs.ndjson`. 【F:app/asgi_gateway.py†L52-L66】【F:app/asgi_gateway.py†L125-L170】
- Client logs aggregated and downloadable via admin export routes; console bridge in client forwards console messages to server (`client.log`). 【F:app/asgi_gateway.py†L300-L365】【F:app/static/js/app.js†L123-L160】
- Admin UI routes for logs and flow tracing (`/admin/logs`, `/api/v1/admin/flow/...`) gated by auth helpers. 【F:app/asgi_gateway.py†L312-L365】

### 3.5 Conversation, Greet, and ASR State Machine (Current)
- **Server turn/voice state machine (EngineV2)**
  - States: READY → LISTENING → THINKING → RESPONDING → CONFIRMING_BARGE (ordered by `_STATE_ORDER`). 【F:app/voice_v2/engine.py†L92-L106】
  - Transitions (guarded by `_ALLOWED_TRANSITIONS` and `_set_state`):
    - READY
      - on `on_audio` when PCM arrives → LISTENING (creates new `turn_id`/`req_id`). 【F:app/voice_v2/engine.py†L1375-L1440】
      - on `on_chat_user` text input → LISTENING (then `THINKING` once text committed). 【F:app/voice_v2/engine.py†L625-L690】
      - on TTS events or barge recovery can go to RESPONDING (allowed transition). 【F:app/voice_v2/engine.py†L100-L105】【F:app/voice_v2/engine.py†L1375-L1511】
    - LISTENING
      - on ASR final commit (`on_asr_final`) → THINKING (LLM pipeline). 【F:app/voice_v2/engine.py†L812-L896】
      - on barge grant triggers → CONFIRMING_BARGE (after `cancel_current_tts`). 【F:app/voice_v2/engine.py†L1109-L1147】
    - THINKING
      - on LLM plan/response start → RESPONDING (when TTS begins). 【F:app/voice_v2/engine.py†L520-L575】【F:app/voice_v2/engine.py†L570-L615】
    - RESPONDING
      - on TTS end (`on_tts_end`) → READY (emits EVT_TURN_END/perf summary). 【F:app/voice_v2/engine.py†L520-L575】【F:app/voice_v2/engine.py†L1441-L1511】
      - on user audio/text barge with policy enabled → CONFIRMING_BARGE (then READY or LISTENING). 【F:app/voice_v2/engine.py†L1078-L1147】
      - on new audio while speaking without barge policy → LISTENING allowed but may be rejected; telemetry logs ignored audio. 【F:app/voice_v2/engine.py†L680-L720】
    - CONFIRMING_BARGE
      - on scheduled confirmation → READY or LISTENING depending on policy result. 【F:app/voice_v2/engine.py†L1109-L1147】
  - Each transition publishes EVT_TURN_STATE breadcrumbs; READY transition emits EVT_TURN_END and perf summary, resetting session metrics. 【F:app/voice_v2/engine.py†L1441-L1511】

- **ASR streaming lifecycle (adapter + EngineV2 hooks)**
  - Stream open triggers: `_schedule_asr_open` when headers validated and session not TTS-blocked; invoked on first user audio header or explicit client `asr.open`, after ensuring `can_open(ctx.session)` and not queued behind active TTS unless `_allow_capture_during_tts` says otherwise. 【F:app/ws/adapter.py†L8361-L8393】
  - Preconditions: no existing open task, policy/session permits (`can_open`), TTS not masking unless allowed, queues reset and turn metrics initialized. 【F:app/ws/adapter.py†L8361-L8411】
  - Open path: `_open_asr` allocates vendor engine, assigns `asr_stream_id`/`req_id`, resolves sample rate/language, and calls `engine.open` with GCP. On success marks session `open`, sets `asr_open=True`, starts no-audio safety net, publishes EVT_ASR_OPEN/EVT_ASR_READY to bus and `asr.ready` bundle to client. 【F:app/ws/adapter.py†L8444-L8605】
  - Audio forwarding: incoming PCM frames are wrapped as WS audio events (seq/byte_count) in EngineV2 `on_audio`; adapter-side ring buffers count `audio_rx_*` metrics before ASR write (details in adapter queues). 【F:app/voice_v2/engine.py†L660-L720】【F:app/ws/adapter.py†L4392-L4440】
  - Stream close conditions: `_close_asr` runs on end-of-turn, timeout, transport close, or errors; cancels open task, closes vendor engine, resets readiness flags, clears VAD/stream ids, and publishes ASR_CLOSED + invariants. 【F:app/ws/adapter.py†L8607-L8681】
  - End-of-utterance handling: EngineV2 `on_asr_final` commits turn and transitions to THINKING; adapter `_handle_asr_result` marks `asr_final_emitted` then `_close_asr` may be invoked by `_end_user_turn`. 【F:app/voice_v2/engine.py†L812-L896】【F:app/ws/adapter.py†L8340-L8359】
  - Silence/vendor timeouts: adapter tracks `no_audio_timeout_deadline_ms` and `_schedule_no_audio_safety_net` (invoked after open) to cancel when idle; `_MIC_OPEN_TIMEOUT_SECONDS` also guards initial mic open during greet. GCP errors like “Audio Timeout” lead to `_close_asr` with reason `timeout` and summary logging. 【F:app/ws/adapter.py†L51-L70】【F:app/ws/adapter.py†L8361-L8411】【F:app/ws/adapter.py†L8607-L8681】
  - Keepalive behavior: client sends 20ms silence chunks; server constants `AUDIO_KEEPALIVE_CHUNK_MS=20` and idle safety nets expect periodic frames. Adapter logs WS_AUDIO_FIRST_CHUNK and uses `_DIAG_NO_AUDIO_CHECK_DELAY_SECONDS` plus `_NO_AUDIO_SAFETY_NET_DEFAULT_MS` to detect stalls; if keepalive missing, ASR safety net fires. 【F:app/ws/adapter.py†L51-L70】【F:app/ws/adapter.py†L113-L150】【F:app/ws/adapter.py†L8361-L8411】

- **Current server gating rules**
  - `GateController` reasons (`tts_active`, `manual_gate`, `system_hold`) determine mic mask; EngineV2 sets gate on TTS start and clears on TTS end or session close. 【F:app/voice_v2/gate.py†L7-L67】【F:app/voice_v2/engine.py†L520-L575】【F:app/voice_v2/engine.py†L520-L546】
  - Adapter guards ASR open via `can_open(ctx.session)` and `_allow_capture_during_tts`; queued arm prevents open while TTS active. 【F:app/ws/adapter.py†L8361-L8393】
  - Audio frames ignored when EngineV2 state not LISTENING (logs `audio_ignored`) ensuring gate by state. 【F:app/voice_v2/engine.py†L680-L720】
  - Policy flags `barge_in_enabled`, `ALLOW_AUDIO_WITHOUT_ASR`, and adapter `client_turn_closed`/`asr_ready` fields gate processing; inferred from `_schedule_asr_open` resets and barge checks. 【F:app/voice_v2/engine.py†L600-L690】【F:app/ws/adapter.py†L8361-L8393】

## 4. Client-Side Architecture (Current)

### 4.1 UI Shell & Application State
- `app.js` bootstraps modules, ensures `window.AppState` with initial phase `greet`, and dynamically loads other JS bundles with version checks. 【F:app/static/js/app.js†L183-L196】【F:app/static/js/app.js†L45-L90】
- Console bridge sends logs to server when WS connected; noisy labels filtered. 【F:app/static/js/app.js†L73-L160】

### 4.2 WebSocket Client & Messaging
- WS client modules (loaded dynamically) expose `getWsClientSocket` etc.; app.js emits telemetry via `logStage`/`emitClientLog` during runtime. 【F:app/static/js/app.js†L1-L28】【F:app/static/js/app.js†L123-L150】
- WS ready phases tracked (`connected`, `ready`) in audio runtime; sending conditioned on WS state. 【F:app/static/js/audio/ws_audio_runtime.js†L32-L37】
- Audio sender invokes `sendAudioChunk`/`sendJSON` hooks provided by ws client; keepalive pings/JSON logs recorded via telemetry. 【F:app/static/js/audio/ws_audio_runtime.js†L161-L194】

### 4.3 Audio Pipeline: Mic → Graph → VAD → PCM Sender
- Capture runtime ensures microphone hardware via `ensureMicHardware` and uses Web Audio contexts from `audio_core`. 【F:app/static/js/app.js†L1-L9】
- `ws_audio_runtime` builds PCM ring buffer (default 1.5s at 16k mono), batches PCM every 60ms, flush timer 50ms. 【F:app/static/js/audio/ws_audio_runtime.js†L19-L37】【F:app/static/js/audio/ws_audio_runtime.js†L95-L146】
- Silence tracking: RMS thresholds with required silent frames, preroll and idle tick timers; audio keepalive sends 20ms silence chunks when idle. 【F:app/static/js/audio/ws_audio_runtime.js†L23-L34】
- PCM energy logging computes dBFS for diagnostics and includes VAD energy metadata. 【F:app/static/js/audio/ws_audio_runtime.js†L56-L94】

### 4.4 Gating Logic on the Client (Current Behavior)
- Sender readiness depends on WS phases (`connected`/`ready`) and external hooks `canCaptureNow`, `isSenderPaused`, `phaseAllowsSend` (passed in). 【F:app/static/js/audio/ws_audio_runtime.js†L161-L194】
- AppState default phase `greet`; comment notes ws_audio_runtime treats missing `turnActive` as true, so PCM can flow unless turn gating set elsewhere. 【F:app/static/js/app.js†L188-L195】
- PCM warm flag set after first frame; keepalive timers manage audio even when paused. 【F:app/static/js/audio/ws_audio_runtime.js†L32-L45】
- Silence detector can trigger banners via `recordClientBannerEvent` when mic data absent (from imported telemetry). 【F:app/static/js/audio/ws_audio_runtime.js†L4-L25】
- Additional gating flags and where they live:
  - `baseEnabled` / `baseEnabledReason` in `ws_audio_runtime` must be true with an active MediaStream (`hasStream`) to form the base gate; toggled by `setBaseEnabled` and cleared on reset. 【F:app/static/js/audio/ws_audio_runtime.js†L737-L758】【F:app/static/js/audio/ws_audio_runtime.js†L1489-L1520】【F:app/static/js/audio/ws_audio_runtime.js†L2048-L2075】
  - `senderPaused` and `pause_reasons` managed via injected `setSenderPauseReason` / `applySenderPausedState`; auto-unpause watchdog clears `greet` pause when all gates satisfied. 【F:app/static/js/audio/ws_audio_runtime.js†L1469-L1540】【F:app/static/js/audio/ws_audio_runtime.js†L1501-L1539】
  - `phaseAllowsSend` derived from AppState phase; allowed when `conversation`, `conversation_ready`, or `user_turn` and WS `wsPhase` is `connected`/`ready`. 【F:app/static/js/audio/ws_audio_runtime.js†L1586-L1664】
  - `micAndPcmReady` equivalent: `baseGate` (baseEnabled + stream) and `hasStream` checks ensure capture stream and pcmSender exist before enabling sender. 【F:app/static/js/audio/ws_audio_runtime.js†L1586-L1687】【F:app/static/js/audio/ws_audio_runtime.js†L1694-L1824】
  - `conversationAsrReady`: `asrReady` flag from AppState gates `shouldSend` unless debug `FORCE_PCM_SEND` override. 【F:app/static/js/audio/ws_audio_runtime.js†L1589-L1636】
  - `turnActive` default true if missing; combined with `senderPaused` and `phaseAllowsSend` to decide `decisionReason` such as `turn_inactive` or `phase_not_ready`. 【F:app/static/js/audio/ws_audio_runtime.js†L1589-L1663】
  - Silence/idle gates: keepalive timers track `lastRealAudioAt`; when idle beyond `AUDIO_KEEPALIVE_IDLE_MS` a `idle_timeout` pause reason is set. 【F:app/static/js/audio/ws_audio_runtime.js†L737-L821】
  - Watchdogs and drop reporting: `maybeAutoUnpauseSender` flips pause off when all gates true; `logAudioDropSummary` (invoked on send failures) emits gate snapshot. 【F:app/static/js/audio/ws_audio_runtime.js†L1469-L1540】【F:app/static/js/audio/ws_audio_runtime.js†L1210-L1255】
- VAD interaction:
  - VAD controller (if provided) influences `canCaptureNow`/`isAudioStreaming`; RMS-based silence detector uses `SILENCE_REQUIRED_FRAMES`, `SILENCE_RMS_THRESHOLD`, `SILENCE_IDLE_TICK_MS`. 【F:app/static/js/audio/ws_audio_runtime.js†L19-L37】【F:app/static/js/audio/ws_audio_runtime.js†L56-L94】
  - `maybeSendAudioKeepalive` only runs when AppState `listening` and streaming; speech start from VAD effectively drives `captureAllowed` and PCM send decisions. 【F:app/static/js/audio/ws_audio_runtime.js†L823-L872】
  - WS/App phases feed the gate: if `wsPhase` not in ready set or phase not allowed, `decisionReason` becomes `ws_not_ready`/`phase_not_ready` and `pcmSender.setEnabled(False)` blocks PCM. 【F:app/static/js/audio/ws_audio_runtime.js†L1586-L1663】【F:app/static/js/audio/ws_audio_runtime.js†L1694-L1824】

### 4.5 Error Handling and Edge Cases
- `gumFailed` flag in ws_audio_runtime prevents reuse of failed capture session; diagnostic hook `window.__askchipShowMicStatus` presents mic errors. 【F:app/static/js/audio/ws_audio_runtime.js†L196-L200】【F:app/static/js/app.js†L31-L43】
- Console bridge avoids infinite loops via guard flag; noisy messages dropped. 【F:app/static/js/app.js†L73-L160】
- Keepalive and silence thresholds act as safeties to avoid ASR timeouts; WS-ready phases required to send audio to avoid drop logging. 【F:app/static/js/audio/ws_audio_runtime.js†L23-L39】

### 4.6 Client Audio & Phase State Machine (Current)
- **Phases and states**
  - Voice phase controller states: `boot` → `greet` → `conversation_ready` → `user_turn` → `conversation_ready` (on turn end) → `closing`/`closed`. 【F:app/static/js/voice/phase_controller.js†L1-L37】
  - AppState initializes `phase="greet"`; `turnActive` treated as true when absent (ws_audio_runtime comment). 【F:app/static/js/app.js†L183-L195】
- **Transitions and triggers (textual state diagram)**
  - boot → greet: page load initializes AppState, console bridge, and mic helpers. 【F:app/static/js/app.js†L183-L236】
  - greet → conversation_ready: greet TTS start/end mark phase via `markGreetStart`/`markGreetEnd`. 【F:app/static/js/voice/phase_controller.js†L17-L31】
  - conversation_ready → user_turn: VAD speech start or ASR arm leads to `enterConversation`; PCM sender still gated by `wsPhase` readiness and `asrReady`. 【F:app/static/js/voice/phase_controller.js†L17-L31】【F:app/static/js/audio/ws_audio_runtime.js†L1586-L1664】
  - user_turn → conversation_ready: server `turn.end` handling sets AppState.turnActive false and voice phase `endUserTurn`. 【F:app/static/js/voice/phase_controller.js†L23-L31】
  - closing/closed: WS close/error sets `wsPhase` to `closing/closed`, making `wsReadyForAudio` false and disabling PCM send. 【F:app/static/js/ws/connection.js†L156-L330】【F:app/static/js/audio/ws_audio_runtime.js†L1586-L1664】
- **Timeline: page load → greet → first user turn (current)**
  1. Page loads; AppState.phase set to `greet`, console bridge patched, mic ensured via `ensureMicHardware`. 【F:app/static/js/app.js†L31-L90】【F:app/static/js/app.js†L183-L236】
  2. WS client connects (`wsPhase` transitions `connecting` → `connected` → `ready`). 【F:app/static/js/ws/connection.js†L156-L330】【F:app/static/js/ws_client.js†L400-L430】
  3. Greet playback: voice phase marks greet start/end; during greet, `senderPaused` may include `greet` until `maybeAutoUnpauseSender` clears when gates allow. 【F:app/static/js/voice/phase_controller.js†L17-L31】【F:app/static/js/audio/ws_audio_runtime.js†L1469-L1540】
  4. Conversation ready: adapter publishes `asr.ready`; AppState.asrReady set, and `computePcmGateSnapshot` sees `phaseAllowsSend` true. 【F:app/ws/adapter.py†L8444-L8605】【F:app/static/js/audio/ws_audio_runtime.js†L1586-L1664】
  5. First PCM send: mic stream acquired, pcmSender initialized; when `baseGate`, `wsReadyForAudio`, `asrReady`, and `turnActive` hold, `updatePcmSenderState` enables sender and logs `client.audio_chunk_send`. 【F:app/static/js/audio/ws_audio_runtime.js†L1469-L1516】【F:app/static/js/audio/ws_audio_runtime.js†L1586-L1687】
  6. Turn end: EngineV2 ASR final → THINKING/RESPONDING; client receives TTS/`turn.end`, phase returns to `conversation_ready`, gating next turn. 【F:app/voice_v2/engine.py†L812-L896】【F:app/static/js/voice/phase_controller.js†L23-L31】

## 5. Data & Persistence (Current)

### 5.1 Database Usage
- Neon Postgres referenced for health/diag checks; DB status used in healthz route. No schema described in code excerpts; admin settings store lazily loaded. 【F:app/asgi_gateway.py†L140-L170】【F:app/config.py†L185-L197】

### 5.2 Profiles, Sessions, and Memory
- Auth handlers manage login/profile and issue WS tokens; session ids used for exporting logs under `exports/<sid>`. 【F:app/asgi_gateway.py†L100-L133】【F:app/asgi_gateway.py†L125-L170】
- Engine tracks per-session turn state and metrics but persistence beyond log export not shown. 【F:app/voice_v2/engine.py†L190-L238】

## 6. Summary of Current Behavior and Constraints
- On page load, app.js sets initial phase, patches console, ensures mic readiness; WS client connects and when in `connected/ready` phases audio runtime can stream PCM16 @16k mono batches with silence keepalives. 【F:app/static/js/app.js†L183-L196】【F:app/static/js/audio/ws_audio_runtime.js†L19-L37】
- Server ASGI gateway routes `/ws/v2/chat` to ChatV2 adapter which validates policy, handles JWT, rate limits, and forwards PCM to GCP ASR; EngineV2 orchestrates turn states, LLM, and TTS responses streamed back over WS. 【F:app/asgi_gateway.py†L188-L235】【F:app/ws/adapter.py†L101-L185】【F:app/voice_v2/engine.py†L114-L174】
- Hard-coded constraints include PCM16 mono 16k requirement, ASR silence/utterance timing from config defaults, mic-open timeouts, heartbeat/ping intervals, and readiness via export directory writeability. 【F:app/config.py†L101-L144】【F:app/ws/adapter.py†L145-L150】【F:app/asgi_gateway.py†L258-L276】

#### 6.1 Current Event Sequence: Greet + First User Turn
1. Client loads page and opens WS (`wsPhase=connected/ready`), sends initial telemetry.
2. Server emits `policy.interaction`/`info`, then greets; TTS start/end propagate to client, voice phase marks greet start/end. 【F:app/voice_v2/engine.py†L520-L575】【F:app/static/js/voice/phase_controller.js†L17-L31】
3. Adapter publishes `asr.ready` bundle after GCP stream opens; client sets `asrReady` and updates PCM gate. 【F:app/ws/adapter.py†L8444-L8605】【F:app/static/js/audio/ws_audio_runtime.js†L1586-L1664】
4. Client begins PCM send once `baseGate` + `wsReadyForAudio` + `asrReady` satisfied; first chunk logged (`client.audio_chunk_send`). 【F:app/static/js/audio/ws_audio_runtime.js†L1586-L1687】【F:app/static/js/audio/ws_audio_runtime.js†L1469-L1516】
5. Adapter forwards PCM to GCP; on final ASR result EngineV2 transitions LISTENING→THINKING→RESPONDING, sends `chat.delta/commit` and TTS start. 【F:app/voice_v2/engine.py†L812-L896】【F:app/voice_v2/engine.py†L520-L575】
6. TTS streamed to client; TTS end triggers EngineV2 READY and adapter `_close_asr`; client phase returns to `conversation_ready`, awaiting next turn. 【F:app/voice_v2/engine.py†L520-L575】【F:app/ws/adapter.py†L8607-L8681】【F:app/static/js/voice/phase_controller.js†L17-L31】
