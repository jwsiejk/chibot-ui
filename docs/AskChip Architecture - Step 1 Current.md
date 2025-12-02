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

### 3.5 Conversation, Greet, and ASR State Machine (Current)
- **Turn states (EngineV2):** READY → LISTENING → THINKING → RESPONDING → CONFIRMING_BARGE. Transitions are restricted by `_ALLOWED_TRANSITIONS`. 【F:app/voice_v2/engine.py†L90-L137】【F:app/voice_v2/engine.py†L1375-L1414】
  - READY
    - on WebSocket open reset → READY (idempotent). 【F:app/voice_v2/engine.py†L243-L269】
    - on greet start request (`start_greet`) emits greet TTS (state stays READY until audio arrives). 【F:app/voice_v2/engine.py†L269-L289】【F:app/voice_v2/engine.py†L569-L604】
    - on first user audio or text input → LISTENING via `_set_state(..., reason="audio_rx"|"text_input")`. 【F:app/voice_v2/engine.py†L418-L470】
  - LISTENING
    - on ASR partial/final or text input completion → THINKING; `_commit_turn_start` is invoked when first audio/text arrives. 【F:app/voice_v2/engine.py†L418-L470】【F:app/voice_v2/engine.py†L722-L770】
    - on TTS start (server response begins) → RESPONDING. 【F:app/voice_v2/engine.py†L569-L604】
  - THINKING
    - on LLM/TTS initiation → RESPONDING. 【F:app/voice_v2/engine.py†L569-L604】【F:app/voice_v2/engine.py†L2142-L2164】
  - RESPONDING
    - on barge attempt (auto VAD/ASR evidence) → CONFIRMING_BARGE with timer, otherwise remains RESPONDING. 【F:app/voice_v2/engine.py†L813-L880】
    - on TTS end → READY (post-hold) or LISTENING if barge reset. 【F:app/voice_v2/engine.py†L569-L604】【F:app/voice_v2/engine.py†L1314-L1384】
  - CONFIRMING_BARGE
    - on confirmation success → LISTENING; on rejection/timeout → READY. 【F:app/voice_v2/engine.py†L831-L880】【F:app/voice_v2/engine.py†L1314-L1384】
- **WS greet hooks:** `ChatV2Adapter` tracks `greet_completed`, emits `tts.start`/`tts.end` from EngineV2; greet TTS uses `_ensure_greet_turn` with turn_index=0. 【F:app/ws/adapter.py†L675-L756】【F:app/ws/adapter.py†L8400-L8454】
- **ASR streaming lifecycle (current):**
  - **Open:** Triggered when `_start_user_turn` prepares ASR and schedules `_open_asr` after turning active; creates GCP stream with `_create_asr_engine` and `_resolve_asr_config` using current policy. 【F:app/ws/adapter.py†L736-L804】【F:app/ws/adapter.py†L8423-L8489】
    - Preconditions: adapter context must have `ws_send` and policy snapshot; rejects duplicate open if engine already live (`asr_open_dedup`). 【F:app/ws/adapter.py†L8425-L8450】
    - Stream id (`asr_stream_id`) and req id (`asr_stream_req_id`) frozen per turn; ASR state marked `opening`/`open` via `mark`. 【F:app/ws/adapter.py†L8423-L8489】
  - **Audio ingress:** PCM frames forwarded from WebSocket through `on_audio_frame` to ASR engine; byte/seq accounting tracked (`asr_bytes_sent`, `asr_first_packet_logged`). 【F:app/ws/adapter.py†L1202-L1318】【F:app/ws/adapter.py†L642-L706】
  - **Close:** `_close_asr` cancels open task, closes engine, resets flags, clears stream ids; invoked on turn end, timeouts, transport close, or ASR final. 【F:app/ws/adapter.py†L8489-L8572】【F:app/ws/adapter.py†L2210-L2235】
    - Silence/timeout: `no_audio_safety_net` and `_MIC_OPEN_TIMEOUT_SECONDS` guard missing audio; ASR engine closure logs `audio_bridge_summary`. 【F:app/ws/adapter.py†L642-L706】【F:app/ws/adapter.py†L8489-L8545】
    - Vendor timeout/errors: ASR timeout sets `ctx.asr_close_reason="timeout"` and publishes `ASR_POST_CLOSE_DROP` if frames arrive after close. 【F:app/ws/adapter.py†L8489-L8545】【F:app/ws/adapter.py†L2280-L2320】
  - **Keepalive:** `AUDIO_KEEPALIVE_CHUNK_MS=20` used for periodic silence frames; adapter schedules server keepalive task emitting `ASR_KEEPALIVE_PING`. Idle safety nets (`_NO_AUDIO_SAFETY_NET_DEFAULT_MS`, `AUDIO_KEEPALIVE_IDLE_MS`) trigger `_close_asr` if audio absent. 【F:app/ws/adapter.py†L51-L67】【F:app/ws/adapter.py†L642-L706】【F:app/static/js/audio/ws_audio_runtime.js†L19-L39】
- **Current server gating rules:**
  - PCM accepted only after header validation and while session not recovering; `_AUDIO_VIOLATION_LIMIT` drops on repeated bad headers. 【F:app/ws/adapter.py†L89-L150】【F:app/ws/adapter.py†L51-L67】
  - `allow_audio_without_asr` flag permits audio when ASR engine unavailable; otherwise `ctx.asr_ready`/`asr_open` must be true before forwarding. 【F:app/ws/adapter.py†L51-L70】【F:app/ws/adapter.py†L642-L706】
  - GateController masks user audio during TTS (`tts_mask_phase`), barge confirmation, or policy holds; inferred from gate events emitted in `on_tts_start`, `cancel_current_tts`, and barge handlers. 【F:app/voice_v2/engine.py†L520-L620】【F:app/voice_v2/engine.py†L813-L880】
  - Session holds (`await_user_expected`, `await_user_after_mask`) delay listen handoff; inferred from adapter fields set around greet/tts end. 【F:app/ws/adapter.py†L642-L756】【F:app/ws/adapter.py†L2150-L2190】【F:app/ws/adapter.py†L8400-L8489】

### 3.4 Logging, Telemetry, and Admin/Debug Facilities
- Telemetry bus installed globally; `FileExporter` writes NDJSON session logs under `exports/<sid>/logs.ndjson`. 【F:app/asgi_gateway.py†L52-L66】【F:app/asgi_gateway.py†L125-L170】
- Client logs aggregated and downloadable via admin export routes; console bridge in client forwards console messages to server (`client.log`). 【F:app/asgi_gateway.py†L300-L365】【F:app/static/js/app.js†L123-L160】
- Admin UI routes for logs and flow tracing (`/admin/logs`, `/api/v1/admin/flow/...`) gated by auth helpers. 【F:app/asgi_gateway.py†L312-L365】

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
  - **Gate inputs (ws_audio_runtime.js):** `baseEnabled`/`baseEnabledReason`, `senderPaused`, `phaseAllowsSend`, `canCaptureNow`, `turnActive`, `wsPhase` (`connected`/`ready`), `asrReady`, `ttsActive`, `micPerm`, and presence of `mediaStream`. `computePcmGateSnapshot` and `updatePcmSenderState` log and decide whether to send. 【F:app/static/js/audio/ws_audio_runtime.js†L1610-L1687】
  - **Pause/auto-unpause:** `isSenderPaused`/`setSenderPauseReason` feed `senderPaused`; `maybeAutoUnpauseSender` clears pause when gates allow. `pause_reasons` are aggregated in provided hooks (`applySenderPausedState`). 【F:app/static/js/audio/ws_audio_runtime.js†L1510-L1580】
  - **Phase + WS gating:** `WS_READY_PHASES={connected,ready}`; `phaseAllowsSend` and `wsPhase` determine `wsReadyForAudio`. AppState phase comes from `AppState.get()` in app.js; phases like `greet`, `conversation_ready`, `user_turn` are read by audio runtime via provided getters. 【F:app/static/js/audio/ws_audio_runtime.js†L19-L45】【F:app/static/js/app.js†L183-L196】
  - **ASR readiness & mic flags:** `conversationAsrReady`/`asrReady` flags and `micAndPcmReady` equivalents appear as `ctx.asr_ready` and `client_mic_open` in adapter-to-client frames; audio runtime checks `asrReady`/`ttsActive` plus `turnActive` before sending. 【F:app/ws/adapter.py†L642-L756】【F:app/static/js/audio/ws_audio_runtime.js†L1610-L1687】
  - **Speech/VAD interaction:** VAD energy from mic frames logged in `logPcmEnergy`; speech start/end surfaced as `client.vad` telemetry and passed to gate updates (sets `speechSeenThisTurn`/`client_vad_speech` in adapter). Client uses RMS thresholds (`SILENCE_RMS_THRESHOLD`, `SILENCE_REQUIRED_FRAMES`) to detect silence and pause sending when silent frames exceed threshold. 【F:app/static/js/audio/ws_audio_runtime.js†L19-L39】【F:app/ws/adapter.py†L642-L706】
  - **Timers/watchdogs:** `scheduleAudioKeepalive` sends 20ms silence when idle; `AUDIO_KEEPALIVE_IDLE_MS` stops keepalive if long idle. Mic acquisition watchdogs in adapter (`_MIC_OPEN_TIMEOUT_SECONDS`) drive banners; client uses `gumFailed` and `micReacquire` for capture recovery. 【F:app/static/js/audio/ws_audio_runtime.js†L19-L45】【F:app/ws/adapter.py†L51-L70】【F:app/static/js/audio/ws_audio_runtime.js†L2000-L2040】

### 4.6 Client Audio & Phase State Machine (Current)
- **Phases (AppState / voice controllers):** boot → greet → conversation_ready → user_turn → turn_end. `AppState` seeded in `app.js` (`phase: 'greet'`) and consumed by audio runtime and WS client modules. 【F:app/static/js/app.js†L183-L196】
- **Transitions:**
  - Page load creates `AppState`, patches console, ensures mic hardware; WS client connects and transitions WS phase to `connected`/`ready` inside audio runtime. 【F:app/static/js/app.js†L45-L150】【F:app/static/js/audio/ws_audio_runtime.js†L19-L45】
  - Server sends `tts.start` for greet → audio runtime keeps baseEnabled off until mic ready; greet completion (`tts.end` with `is_greet`) toggles `greet_completed` in adapter and allows `await_user_expected` listen handoff. 【F:app/ws/adapter.py†L642-L756】【F:app/ws/adapter.py†L8489-L8545】
  - When adapter signals `input.start`/`asr.input.start` or client VAD fires speech start, `turnActive`/`asr_turn_active` set true and `phaseAllowsSend` enables PCM streaming. 【F:app/ws/adapter.py†L642-L706】【F:app/static/js/audio/ws_audio_runtime.js†L1610-L1687】
  - Turn end (`turn.end`, `asr.final`, or TTS end) resets `turnActive`, updates `baseEnabledReason`, and closes ASR stream on server; client remains in conversation_ready until next `user_turn`. 【F:app/ws/adapter.py†L8489-L8572】【F:app/voice_v2/engine.py†L1314-L1384】
- **Timeline for page load → greet → first user turn:**
  1. Page load: `app.js` sets phase `greet`, loads bundles, calls `ensureMicHardware`; WS connection initiated. 【F:app/static/js/app.js†L45-L196】
  2. WS ready: audio runtime marks `wsPhase` to `connected`/`ready`; PCM sender warmed but gated by `baseEnabled` false until mic stream present. 【F:app/static/js/audio/ws_audio_runtime.js†L19-L45】【F:app/static/js/audio/ws_audio_runtime.js†L2000-L2040】
  3. Greet: server emits `tts.start`/`tts.end` (is_greet) → adapter sets `greet_completed`; GateController masks user audio during TTS. 【F:app/voice_v2/engine.py†L520-L620】【F:app/ws/adapter.py†L675-L756】
  4. Conversation ready: adapter marks `await_user_expected`/`client_capture_armed`; client `baseEnabled` set true once mic stream acquired, `phaseAllowsSend` true; PCM keepalives start. 【F:app/ws/adapter.py†L642-L756】【F:app/static/js/audio/ws_audio_runtime.js†L1510-L1580】
  5. First speech: VAD detects speech start (RMS threshold) → `handlePcmFrame` logs `speechSeenThisTurn`, `turnActive` true, PCM batches flushed every 60ms via `handlePcmSend`. 【F:app/static/js/audio/ws_audio_runtime.js†L19-L39】【F:app/static/js/audio/ws_audio_runtime.js†L1400-L1465】
  6. Turn end: ASR final from server drives EngineV2 to THINKING/RESPONDING; adapter closes ASR after TTS end and resets gates to READY. 【F:app/voice_v2/engine.py†L722-L770】【F:app/voice_v2/engine.py†L1314-L1384】【F:app/ws/adapter.py†L8489-L8572】

### 4.5 Error Handling and Edge Cases
- `gumFailed` flag in ws_audio_runtime prevents reuse of failed capture session; diagnostic hook `window.__askchipShowMicStatus` presents mic errors. 【F:app/static/js/audio/ws_audio_runtime.js†L196-L200】【F:app/static/js/app.js†L31-L43】
- Console bridge avoids infinite loops via guard flag; noisy messages dropped. 【F:app/static/js/app.js†L73-L160】
- Keepalive and silence thresholds act as safeties to avoid ASR timeouts; WS-ready phases required to send audio to avoid drop logging. 【F:app/static/js/audio/ws_audio_runtime.js†L23-L39】

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
1) Client opens WebSocket (`ws.open`), adapter emits `EVT_WS_OPEN`, resets session to READY. 【F:app/voice_v2/engine.py†L243-L269】
2) Server emits policy/info frames; greet started (`tts.start` is_greet) and streamed to client. 【F:app/voice_v2/engine.py†L269-L304】【F:app/voice_v2/engine.py†L569-L604】
3) Client keeps PCM gated until mic ready; baseEnabled flips true after capture and WS `ready`. 【F:app/static/js/audio/ws_audio_runtime.js†L19-L45】【F:app/static/js/audio/ws_audio_runtime.js†L2000-L2040】
4) Greet completes (`tts.end` is_greet), adapter sets `greet_completed` and arms listen (`await_user_expected`). 【F:app/ws/adapter.py†L642-L756】【F:app/ws/adapter.py†L8489-L8545】
5) First user speech: client VAD detects speech start → PCM batches flow; adapter marks `input.start`/ASR open, forwards frames to GCP. 【F:app/static/js/audio/ws_audio_runtime.js†L19-L39】【F:app/ws/adapter.py†L1202-L1318】【F:app/ws/adapter.py†L8423-L8489】
6) ASR partial/final events transition EngineV2 LISTENING→THINKING; LLM/NLG run. 【F:app/voice_v2/engine.py†L722-L770】
7) TTS begins (`tts.start`) → RESPONDING; on barge check possible CONFIRMING_BARGE; after `tts.end` state returns READY and ASR closes. 【F:app/voice_v2/engine.py†L569-L620】【F:app/voice_v2/engine.py†L813-L880】【F:app/ws/adapter.py†L8489-L8572】
