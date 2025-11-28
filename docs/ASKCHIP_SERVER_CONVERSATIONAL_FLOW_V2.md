# AskChip Server Conversational Flow — v2

## Overview
ChatV2Adapter coordinates WS lifecycle, greet orchestration, ASR/LLM/TTS, and telemetry. This document describes the current behavior with code references.

## State & Session Model (Server)
- `_AdapterContext` holds per-connection state: ASR gates, greet completion, audio acceptance, turn indices, VAD hints, and tracking flags (e.g., `greet_completed`, `accepting_audio`, `asr_ready_bundle_sent_ms`, `turn_req_id`).【F:app/ws/adapter.py†L600-L752】
- Engine/adapter maintain `turn_index` and `turn_req_id` on the context for correlating ASR/LLM/TTS events.【F:app/ws/adapter.py†L600-L706】

## Lifecycle: Full Server Conversational Flow

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

## Utopia Server Conversational Architecture
- Clear separation: greet TTS plays while ASR remains closed; ASR opens only after explicit greet completion and ASR-ready bundle acknowledgment.
- Turn lifecycle: deterministic `turn_index`/`turn_req_id` issuance, ASR timeout handling, and policy decisions logged once per turn.
- Barge-in: explicit interrupt signals from client pause/cancel active TTS and reopen ASR quickly.
- Logging: concise per-turn telemetry with aggregated client logs and reduced noise.

## Gap Analysis: Server (As Implemented vs Utopia)
- **Match**: Greet start/complete signaling with metadata tagging; greet-phase PCM rejection; ASR ready gated until greet completion.【F:app/ws/adapter.py†L1322-L1539】【F:app/ws/adapter.py†L3965-L3990】
- **Partial**: ASR readiness relies on `_ensure_asr_ready` after greet but timing depends on bundle emission; context tracks VAD/client cues yet barge-in policy is implicit rather than enforced.【F:app/ws/adapter.py†L1235-L1283】【F:app/ws/adapter.py†L600-L720】
- **Mismatch**: No explicit server-side interrupt to cancel streaming TTS on client barge-in; LLM/policy decision flow not surfaced here, leaving turn-handling transparency limited.
