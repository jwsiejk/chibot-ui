AskChip — GPT Session Baseline (Code-Aware, Fix-Agnostic)

Purpose: This is the authoritative snapshot of how AskChip works today so GPT can interpret logs correctly and stay aligned with your runtime. No redesigns, no guesses, no renames.

0) Ground Rules for GPT

Do not propose architectural changes (WebRTC, microservices, GRPC, etc.) unless explicitly asked.

Do not rename or relocate existing functions, events, or files listed here.

Do not conflate lanes:
– Telemetry lane = noisy tech signals (emitVoiceEvent)
– Flow lane = human milestones (emitFlowBreadcrumb)
Heartbeats and low-level audio signals never go to Flow.

When reasoning from logs, map each log to a module + function in this document before suggesting anything.

1) Transport & Session

Single control WebSocket for everything: wss://…/ws/v1/chat?session_id=<sid>

Auth: subprotocol bearer.<token> (fetched by /api/v1/auth/ws-token)

All turns (including the very first assistant intro) travel over this WS and use the same LLM→TTS→audio pipeline.

Start gate: A session begins only after Start in the UI.

Client WS module: static/js/ws.js

Connect/backoff, KeepAlive → KeepAliveAck, message dispatch, onReconnect hooks.

Server WS module: app/ws/ws_asgi.py

Parses client JSON via app/ws/schema_v1.py, routes messages, sends assistant chunks, UtteranceEnd, errors, KeepAliveAck.

2) Audio & Voice Runtime (Client)

Owner: static/js/voice/runtime/AdaptiveRuntime.js

Creates/stops MediaRecorder (Opus in OGG/WebM), sets timeslice, wires ondataavailable.

Policy gating: VAD, TTS mask/quarantine (suppresses local VAD while Chip speaks).

Barge-in wiring: PTT (“Hold to Talk”) is for interrupting Chip during TTS, not for every user turn.

Playback: static/js/audio.js, static/js/audio_player.js

HTMLAudio/MSE; emits playback OK/error + heartbeats.

Bootstrap: static/js/bootstrap.js

Start behavior, barge-in UI (button + Spacebar), post-TTS VAD re-arm timing.

3) ASR Backend (Deepgram)

Client: app/services/streaming_asr/deepgram_client.py

Builds DG WS URL. With containerized Opus (OGG/WebM), it omits encoding, sample_rate, channels from the query (unless explicitly overridden).

DG KeepAlive: sends text {"type":"KeepAlive"} during idle; manages open/close; logs dg_* telemetry.

Sends {"type":"CloseStream"} on shutdown.

4) LLM / Turn Runner (Server)

Glue: app/services/streaming.py

Runs assistant turns (including the intro turn): collect model output → TTS schedule → emits assistant_end (text) and UtteranceEnd (audio end).

Any greet-named hooks are kickoff/guard helpers, not a separate pipeline.

5) Policy & Gates

Policy hub: app/policy/policy.py (+ siblings)

Teacher moves, evidence/threshold gates, quarantine decisions, turn-open/commit gates.

Logs appear as policy_decision, teacher_move:*, gate_open|gate_blocked.

GPT should treat policy decisions as the source of truth for why a turn opened/blocked; do not guess at timing in other layers until you read policy logs.

6) Events & Telemetry (Canonical)
Lanes

Telemetry (noisy): emitVoiceEvent(eventName, payload)
Low-level signals (audio heartbeats, recorder state, bytes counters, keepalives, DG signals).

Flow (concise): emitFlowBreadcrumb(eventName, payload)
Milestones only (e.g., session_ready, greet_start, tts_play_started, utterance_end, ws_close).

Client audio emit wrappers (telemetry lane)

emitClientAudioEvent(ctx, type, detail, turnId)

emitClientAudioContextState(ctx, detail, turnId) → { state:'running'|'suspended', error? }

emitClientAudioHeartbeat(ctx, turnId) → { path:'playback'|'capture', … }

Heartbeats and client-audio signals never go to the Flow lane.

7) Message Types (Essentials)

Client → Server (subset):
Configure, KeepAlive, user turn triggers, intro kickoff, close/end controls.

Server → Client (subset):
KeepAliveAck, assistant text segments + assistant_end, UtteranceEnd, errors, TTS audio chunks.

The first assistant intro is just the first assistant turn on this protocol.

8) File Map (Index GPT should anchor to)

Client WS: static/js/ws.js

Voice runtime: static/js/voice/runtime/AdaptiveRuntime.js

Bootstrap: static/js/bootstrap.js

Playback: static/js/audio.js, static/js/audio_player.js

Schema: app/ws/schema_v1.py

WS server: app/ws/ws_asgi.py

Turn runner: app/services/streaming.py

Deepgram: app/services/streaming_asr/deepgram_client.py

Flow/telemetry (server): app/flow/emit.py, app/flow/trace.py

Client Flow sender: static/js/flow_breadcrumbs.js

Policy: app/policy/policy.py (+ peers)

9) Log-to-Code Cheat Sheet (how GPT should read your logs)

Control WS liveness

KeepAlive (→) from static/js/ws.js::_startKeepAlive()

KeepAliveAck (←) from app/ws/ws_asgi.py via schema_v1.make_keepalive_ack()

Deepgram liveness

dg_keepalive_tx, dg_keepalive_miss_streak, dg_soft_reconnect from deepgram_client.py::_keepalive_loop()

Intro/first assistant turn

greet_start (kickoff), assistant chunks, assistant_end, UtteranceEnd

Routed by ws_asgi.py; executed by services/streaming.py

Playback signals

Flow: tts_play_started

Telemetry: client_audio_play_ok / client_audio_play_error, client_audio_heartbeat {path:'playback'}

From audio.js/audio_player.js

Capture signals

Telemetry: client_audio_context_state, client_audio_heartbeat {path:'capture'}, recorder_restart, asr_start

From AdaptiveRuntime.js (+ WS reconnect hook in ws.js)

Policy/gates

policy_decision, teacher_move:*, gate_open|gate_blocked from policy.py

Socket close

Flow: ws_close {code, initiator, reason} from client (ws.js) and server (ws_asgi.py)

10) “If you see X, it implies Y (and where)”

KeepAlive without KeepAliveAck → check server handler in ws_asgi.py, and client timer in ws.js.

DG closes after idle with preceding dg_keepalive_tx → verify KA cadence in deepgram_client.py vs. last audio; confirm KA loop running during TTS/quarantine.

tts_play_started but silence → check client_audio_play_error and client_audio_context_state (autoplay/muted) in audio.js.

Reconnect with no mic → absence of recorder_restart/asr_start implies AdaptiveRuntime didn’t recreate recorder or WS reconnect hook didn’t trigger.

barge_in fired with no PTT → inspect AdaptiveRuntime TTS mask/quarantine + policy.py gates; confirm PTT wiring in bootstrap.js.

No UtteranceEnd → services/streaming.py didn’t drain TTS fully or emit the end event.

ws_close 1001 after quiet period → control WS keepalive likely paused/blocked; confirm KeepAlive cadence in ws.js and server ack.

11) Behavioral Clarifications (to avoid classic misreads)

Hybrid voice mode: normal user turns proceed automatically when policy allows; PTT is for interrupting Chip during TTS (not mandatory for every turn).

Intro turn is not a special pipeline: same machinery as any assistant turn; kickoff/dupe guards are wrappers only.

Two keepalive layers: control WS (browser↔server) and DG WS (server↔Deepgram) are independent and both expected to exist.

Containerized Opus rule: When sending containerized Opus, do not add encoding/sample_rate/channels to DG query unless explicitly requested.

12) Minimal Baseline Signals (quick pre-analysis checklist)

Before diagnosing, GPT should confirm presence (or absence) of:

Control WS KeepAlive ↔ KeepAliveAck pairs in the first minute

DG idle periods showing dg_keepalive_tx (no idle timeouts)

First assistant turn shows assistant_end + UtteranceEnd

Playback shows tts_play_started and client_audio_play_ok/error

Capture shows client_audio_context_state at start and client_audio_heartbeat {path:'capture'} during speech

Policy decisions present around gating points

Close telemetry ws_close {code, initiator, reason} on shutdown

If any are missing, GPT should name which module likely failed to emit and why, using the map above—before offering any steps.

13) What GPT Must Not Do in This Session

Change architecture, message names, or file locations listed here.

Move heartbeats/low-level audio telemetry into the Flow lane.

“Helpfully” add raw audio params to DG when using containerized Opus.

Assume “PTT for every user turn.” It’s interrupt-only unless told otherwise.

End of Baseline.
(From here, you can share logs and questions. GPT should diagnose by mapping logs to the modules above—no assumptions, no rewrites.)