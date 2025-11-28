# AskChip Server Conversational Flow — v2

## Overview
ChatV2Adapter coordinates WS lifecycle, greet orchestration, ASR/LLM/TTS, and telemetry. This document describes the current behavior with code references.

## State & Session Model (Server)
- `_AdapterContext` holds per-connection state: ASR gates, greet completion, audio acceptance, turn indices, VAD hints, and tracking flags (e.g., `greet_completed`, `accepting_audio`, `asr_ready_bundle_sent_ms`, `turn_req_id`).【F:app/ws/adapter.py†L600-L752】
- Engine/adapter maintain `turn_index` and `turn_req_id` on the context for correlating ASR/LLM/TTS events.【F:app/ws/adapter.py†L600-L706】

## Lifecycle: Full Server Conversational Flow

See the implemented timeline below for the concrete ordering of adapter/engine steps.

### WS Open & Handshake
- `asgi_gateway` instantiates `ChatV2Adapter`/`EngineV2` and routes `/ws/v2/chat` connections to the adapter singleton.【F:app/asgi_gateway.py†L1107-L1169】
- `_on_open_and_greet` runs on connection: logs `ws.open_and_greet`, invokes engine `on_open`, then `start_greet`, and clears `await_user_*` flags to avoid premature re-arm.【F:app/ws/adapter.py†L7170-L7188】

### Greet Generation & TTS
- TTS start handling marks frames with `meta.is_greet` when `_frame_signals_greet` matches, emits `greet.start`, and records `greet_utt_id` for completion tracking.【F:app/ws/adapter.py†L1322-L1362】
- TTS end detects greet completion via markers or tracked `greet_utt_id`, emits `greet.completed/server.greet_complete`, and sends `greet.complete` frame to the client; greet completion also triggers ASR-ready arm when bundle not yet sent.【F:app/ws/adapter.py†L1453-L1539】

### Greet → ConversationReady (Server)
- After greet completion, `_ensure_asr_ready` runs once to emit ASR readiness; pre-greet ASR open attempts are suppressed with `asr_ready_suppressed_during_greet` logging.【F:app/ws/adapter.py†L1235-L1283】【F:app/ws/adapter.py†L1537-L1539】

### ASR Streaming & Turn Handling
- Incoming PCM frames publish `audio.frame.received`; if greet not complete, server rejects audio with `audio_not_expected` and closes frame handling.【F:app/ws/adapter.py†L3965-L3990】
- ASR state flags (`asr_open`, `asr_ready`, timers, watchdogs) reside on the context; accepting_audio governs ingestion and rate limits in the same structure.【F:app/ws/adapter.py†L600-L646】

### LLM & TTS Responses
- TTS start/end events log provider/utt metadata and update session metrics; greet detection piggybacks on these handlers as above.【F:app/ws/adapter.py†L1322-L1522】
- Engine-driven TTS uses ElevenLabs by default via provider metadata in emitted frames.【F:app/ws/adapter.py†L1322-L1337】

### Barge-in / Interrupt Handling
- Context tracks `client_vad_*` signals and `await_user_*` flags, but no explicit server-side barge-in cancelation logic is visible in these sections; output truncation is not triggered by client interrupts in the shown code.【F:app/ws/adapter.py†L676-L720】

### Error Handling / Disconnects
- Audio ingestion rejects over-limit frames with `frame_too_large` errors; greet-phase PCM causes `audio_not_expected`. The context includes multiple watchdog timers (`no_audio_watchdog`, `asr_ready_deadline_task`) to cancel on errors.【F:app/ws/adapter.py†L3965-L4010】【F:app/ws/adapter.py†L600-L706】

### Logging & Telemetry
- Adapter emits `session_step` events such as `ws.open_and_greet`, `audio.frame.received`, `greet.start/complete`, and TTS markers for downstream telemetry.【F:app/ws/adapter.py†L1322-L1539】【F:app/ws/adapter.py†L3965-L3990】【F:app/ws/adapter.py†L7170-L7180】
- Logging noise is tuned globally in `asgi_gateway` with `tune_logging_noise()` and per-category log levels for ws/auth/tts modules.【F:app/asgi_gateway.py†L45-L67】

## End-to-End Timeline (As Implemented)

1. **User hits Start** — Client click handler mints a WS token and opens `/ws/v2/chat` with `chat.v2` + JWT subprotocols; adapter singleton is constructed via `_get_adapter`. Telemetry: `evt=ws_open_attempt`.【F:app/static/js/app.js†L1891-L1979】【F:app/asgi_gateway.py†L1107-L1116】 _(Initiator: client; Phase: connect/greet)_
2. **WS `/ws/v2/chat` connect** — ASGI gateway routes the socket to `ChatV2Adapter`; `_on_open_and_greet` logs `ws.open_and_greet`, invokes `EngineV2.on_open`, and immediately calls `start_greet`.【F:app/ws/adapter.py†L7170-L7188】【F:app/voice_v2/engine.py†L252-L270】 _(Initiator: server; Phase: greet)_
3. **Greet start (TTS)** — Engine emits TTS greet; adapter `_handle_tts_start` tags `meta.is_greet`, records `greet_utt_id`, and publishes `tts.start/greet.start`.【F:app/ws/adapter.py†L1322-L1362】 _(Initiator: server; Phase: greet)_
4. **Greet end** — On `tts.end`, adapter matches `greet_utt_id`, emits `greet.completed` + `server.greet_complete`, and sends `greet.complete` to the client; if greet just finished, it calls `_ensure_asr_ready` and logs `server.conversation_ready`.【F:app/ws/adapter.py†L1399-L1549】 _(Initiator: server; Phase: greet→conversation_ready)_
5. **ASR open / `asr_ready_bundle`** — `_ensure_asr_ready` defers pre-greet attempts, schedules `_open_asr`, waits for the open task, then emits `asr.ready` + `input.start` + `start_listening` via `_send_asr_ready_bundle`. Telemetry: `asr_ready_emit`, `asr_ready_after_greet`.【F:app/ws/adapter.py†L1214-L1289】 _(Initiator: server; Phase: conversation_ready)_
6. **ConversationReady/UserTurn arm** — Adapter logs `server.conversation_ready` when ASR ready follows greet, and will send `turn.begin` if no turn is active post-greet to allow mic streaming.【F:app/ws/adapter.py†L1524-L1556】 _(Initiator: server; Phase: conversation_ready)_
7. **First user utterance → ASR ingest** — Binary PCM arrives as `_handle_binary` (`audio.frame.received`); greet-incomplete frames are rejected with `audio_not_expected`. ASR results flow into `_handle_asr_result`, which publishes `asr.partial`/`asr.final` session steps and defers policy to Engine.【F:app/ws/adapter.py†L3964-L3991】【F:app/ws/adapter.py†L7586-L7772】 _(Initiator: client audio; Phase: user_turn)_
8. **LLM + dialog policy** — EngineV2 consumes `asr.final` via `on_asr_final`, records `turn_id/req_id`, emits NLU, and drives policy/NLG selection before TTS synthesis begins.【F:app/voice_v2/engine.py†L629-L713】 _(Initiator: server; Phase: thinking→responding)_
9. **TTS synthesis + playback** — Engine streams TTS PCM through adapter `_handle_tts_start/_handle_tts_end`, publishing `tts.start/tts.end` session steps and pushing audio frames back to the client. Greet completion and conversation-ready logging live in these handlers.【F:app/ws/adapter.py†L1322-L1549】 _(Initiator: server; Phase: responding)_
10. **Turn end** — Empty finals/timeouts trigger `turn.empty` and `_end_user_turn`; final handling logs `client_turn_stop` and closes ASR as needed.【F:app/ws/adapter.py†L3940-L3963】【F:app/ws/adapter.py†L7790-L7820】 _(Initiator: server; Phase: closing)_
11. **Session close** — Client end button or network close drives `_close_asr` and socket teardown; adapter error paths propagate close codes (`audio_not_expected`, `frame_too_large`).【F:app/static/js/app.js†L1992-L2013】【F:app/ws/adapter.py†L3980-L4003】 _(Initiator: client/server; Phase: closed)_

## Server-side View of One Turn (Post-Greet)

```mermaid
sequenceDiagram
    participant Client
    participant ChatV2Adapter
    participant EngineV2
    participant ASR as ASR provider
    participant LLM
    participant TTS as TTS provider

    Client->>ChatV2Adapter: PCM binary frame (audio.frame.received)【F:app/ws/adapter.py†L3964-L3991】
    ChatV2Adapter-->>ASR: feed audio (asr stream open)
    ASR-->>ChatV2Adapter: asr.partial/asr.final【F:app/ws/adapter.py†L7586-L7772】
    ChatV2Adapter->>EngineV2: on_asr_partial / on_asr_final【F:app/ws/adapter.py†L7733-L7754】【F:app/ws/adapter.py†L7756-L7772】
    EngineV2->>LLM: dialog policy + LLM decision (NLG)【F:app/voice_v2/engine.py†L700-L713】
    EngineV2-->>TTS: synthesize reply
    TTS-->>ChatV2Adapter: tts.start / tts.end frames【F:app/ws/adapter.py†L1322-L1549】
    ChatV2Adapter-->>Client: send TTS audio + markers (greet.complete/tts.*)【F:app/ws/adapter.py†L1345-L1355】【F:app/ws/adapter.py†L1525-L1549】
```

## Utopia Server Conversational Architecture
- Clear separation: greet TTS plays while ASR remains closed; ASR opens only after explicit greet completion and ASR-ready bundle acknowledgment.
- Turn lifecycle: deterministic `turn_index`/`turn_req_id` issuance, ASR timeout handling, and policy decisions logged once per turn.
- Barge-in: explicit interrupt signals from client pause/cancel active TTS and reopen ASR quickly.
- Logging: concise per-turn telemetry with aggregated client logs and reduced noise.

## Gap Analysis: Server (As Implemented vs Utopia)
- **Match**: Greet start/complete signaling with metadata tagging; greet-phase PCM rejection; ASR ready gated until greet completion.【F:app/ws/adapter.py†L1322-L1539】【F:app/ws/adapter.py†L3965-L3990】
- **Partial**: ASR readiness relies on `_ensure_asr_ready` after greet but timing depends on bundle emission; context tracks VAD/client cues yet barge-in policy is implicit rather than enforced.【F:app/ws/adapter.py†L1235-L1283】【F:app/ws/adapter.py†L600-L720】
- **Mismatch**: No explicit server-side interrupt to cancel streaming TTS on client barge-in; LLM/policy decision flow not surfaced here, leaving turn-handling transparency limited.
