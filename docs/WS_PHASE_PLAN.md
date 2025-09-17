WS_PHASE_PLAN - When complete with a phase mark it complete in this document.  Then validate the full document is still in place after you mark complete -- DO NOT EVER DELETE ANYTHING FROM THIS DOCUMENT 

Purpose: Migrate Ask Chip from the hybrid HTTP+WS audio lane to a WebSocket-only design that mirrors Deepgram’s realtime WebSocket patterns (binary audio frames, {"type":"CloseStream"} to end a turn, interim/final Results with is_final, UtteranceEnd, and keep-alive). This document is the single source of truth for scope, protocol, phases, acceptance criteria, and runbook notes.

High-Level Overview

One socket per tab: /ws/v1/chat carries mic audio (binary), control (JSON), assistant output/events (JSON), and optionally streamed TTS (binary).

Delete HTTP mic routes: /api/v1/voice/chunk, /api/v1/voice/end. No fallbacks, no hybrids, no mocks.

Keep existing v1 HTTP routes: /api/v1/greet, /api/v1/chat, /api/v1/voice/tts-with-visemes, /api/v1/admin/logs (SSE).

Provider semantics: Follow Deepgram WS shapes: binary audio frames, {"type":"CloseStream"}, pass-through Results (channel.alternatives[].transcript, is_final), optional UtteranceEnd, keep-alive.

WebSocket Message Schema (/ws/v1/chat)

Client → Server (Text JSON)

{"type":"Configure","encoding":"opus","sample_rate":48000,"channels":1,"interim_results":true,"smart_format":true,"punctuate":true,"vad_events":true,"utterance_end_ms":1200} (optional; mirrors Deepgram flags; once after connect)

{"type":"CloseStream"} — end the user turn (server forwards provider close)

{"type":"KeepAlive"} — every ~4s during long silences

{"type":"BargeIn"} — app-level: stop TTS immediately and close current turn

Client → Server (Binary)

Mic audio slices (ArrayBuffer) from MediaRecorder('audio/webm;codecs=opus') at ~150–200 ms cadence. First blob must include the WebM/Opus header (send blobs unmodified).

Server → Client (Text JSON)

{"type":"Results","channel":{"alternatives":[{"transcript":"...","confidence":0.93}],"is_final":false}}

{"type":"Results", ... "is_final":true} (final for the span)

{"type":"UtteranceEnd"}

{"type":"Error","code":"...","message":"..."}

Server → Client (Binary, optional)

If you stream TTS on the same socket: TTSStart (text) → binary audio chunks → TTSEnd (text). Otherwise keep TTS on /api/v1/voice/tts-with-visemes.

Phase 0 — Hard reset to “WS-only” goal

Goal: lock scope, kill hybrid, keep v1 surfaces clean.

Tasks

Kill hybrid endpoints & references

Delete server routes: /api/v1/voice/chunk, /api/v1/voice/end (and any helpers).

Remove all client code that ever calls those endpoints.

Delete tests that exercise HTTP chunking.

Linter / acceptance guard

Route-linter fails build on any /api/v1/voice/* (except TTS if you keep it HTTP), and on any legacy/“/api/greet” etc.

Flags & config

Remove FEATURE_AUDIO_HTTP (or similar). No runtime toggle—WS is the only lane.

Keep existing v1 surfaces:

WebSocket: /ws/v1/chat (single per tab)

HTTP (unchanged): /api/v1/greet, /api/v1/chat, /api/v1/voice/tts-with-visemes, /api/v1/admin/logs

Acceptance checks

Grep: no references to /api/v1/voice/chunk or /api/v1/voice/end.

Linter blocks any reintroduction of those routes.

Phase 1 — WS protocol schema (Deepgram-aligned)

Goal: fix the contract once so FE and BE code meet in the middle (no “inventing” later).

Message kinds on /ws/v1/chat:

Client→Server (text JSON)

{"type":"Configure","encoding":"opus","sample_rate":48000,"channels":1,"interim_results":true,"smart_format":true,"punctuate":true,"vad_events":true,"utterance_end_ms":1200}
(Optional; mirrors Deepgram query flags.)

{"type":"CloseStream"} → end user turn (server forwards close to provider). 
developers.deepgram.com

{"type":"KeepAlive"} every ~3–5 s when idle (prevents provider idle close; send as text, not binary). 
developers.deepgram.com

{"type":"BargeIn"} (app-level: stop TTS, close current turn, prep next).

Client→Server (binary)

Raw mic slices (ArrayBuffer) from MediaRecorder('audio/webm;codecs=opus'), first blob includes header, cadence 150–200 ms. (Don’t coalesce the first chunk.) 
GitHub

Server→Client (text JSON)

Pass-through partials/finals from provider:

{ "type":"Results",
  "channel": { "alternatives":[{"transcript":"...","confidence":0.93}],
               "is_final": false } }


(is_final:true marks the final for that span.) 
developers.deepgram.com

{"type":"UtteranceEnd"} (when vad_events=true and utterance_end_ms >= 1000). 
developers.deepgram.com

{"type":"Error","code":"…","message":"…"}

(Optional) Server→Client (binary) for TTS streaming if you want TTS over the same socket; otherwise leave TTS on your existing HTTP route.

Acceptance checks

Contract documented in repo (docs/ws_protocol.md).

FE/BE unit tests validate text vs binary demux, and that CloseStream produces a final.

Phase 2 — Backend: WS handler (single file focus)

Goal: make /ws/v1/chat the only audio/control lane.

Server changes

Demux frames

In /ws/v1/chat, async for message in websocket:

Text JSON: handle Configure, CloseStream, KeepAlive, BargeIn.

Binary: forward to provider stream.

Provider session lifecycle

On the first audio frame (or on Configure if you prefer), open provider WS once:

Gate with an asyncio.Lock or per-session open event to avoid “send before open”.

Emit asr_open and mirror to Admin SSE.

On each binary frame: send_chunk.

On CloseStream: provider {"type":"CloseStream"}; linger ~600 ms for stragglers; wait (bounded ~8 s) for the final. Emit UtteranceEnd if provider sends it.

Ping/Pong

WebSocket server ping every ~25–30 s; drop stale connections on missed pings (client also sends KeepAlive). 
developers.deepgram.com

Admin SSE

Echo asr_partial, asr_final, asr_error, and UtteranceEnd to /api/v1/admin/logs for observability.

Acceptance checks

Exactly one provider open per WS connection.

No “send_before_open” errors while audio is streaming.

Final result is emitted after CloseStream within the timeout.

Phase 3 — Deepgram glue (wire to provider semantics)

Goal: translate schema to provider calls 1:1.

Tasks

Build provider URL with flags from either defaults or the Configure frame (encoding, sample_rate, channels, interim_results, vad_events, utterance_end_ms ≥ 1000). 
developers.deepgram.com

Forward binary audio frames as provider binary WS frames.

On user turn close, send provider {"type":"CloseStream"} so Deepgram flushes any remaining audio and returns final transcripts. 
developers.deepgram.com
+1

Surface interim/final Results back to the client unmodified (channel.alternatives[].transcript, is_final). 
developers.deepgram.com

Optionally surface UtteranceEnd when VAD is enabled (vad_events=true, utterance_end_ms set). 
developers.deepgram.com

Acceptance checks

Configure values appear in provider URL.

Interim results start within ~1 s of speaking; final appears after CloseStream. 
developers.deepgram.com

Phase 4 — Frontend: mic sender & controls (WS-only)

Goal: remove all POST logic; use the single socket for audio + control.

Tasks

MediaRecorder → WS

rec.start(150 or 200); on ondataavailable, send the blob as-is (ArrayBuffer) over the WS.

Backpressure: if socket.bufferedAmount grows beyond N bytes, rec.pause(); resume when drained.

Start/Stop/Barge-in: send JSON frames per schema; on barge-in, immediately stop/pause TTS.

UI & dots

Listening → show live interims; Thinking → after UtteranceEnd or CloseStream; Responding → when TTS begins.

KeepAlive

Send {"type":"KeepAlive"} every ~4 s during long silences to avoid idle closure upstream. 
developers.deepgram.com

Acceptance checks

First chunk (with header) is sent intact; server never logs “drop small first chunk” for the first real blob.

Interims appear continuously while talking.

Barge-in stops TTS immediately and readies next turn.

Phase 5 — TTS & persona (no regression)

Goal: keep Chip sounding human and on-persona.

Tasks

Keep ElevenLabs voice + viseme timing as-is; or stream TTS audio over WS if you prefer single-lane I/O.

Persona/prompting unchanged; keep teacher-move selection logic.

Re-inject a one-line persona reminder every N turns to prevent drift.

Acceptance checks

TTS latency within your budget; barge-in stops TTS within ~150–250 ms.

Persona intensity (12–15%) maintained per your config.

Phase 6 — Diagnostics & Admin

Goal: test WS path only; observe the same events the server sees.

Tasks

Diagnostics page: watch Admin SSE (or the chat WS) for asr_partial, asr_final, asr_error tied to the current session_id; remove HTTP chunk tests.

Add “Record 5 s” prompt that clearly shows Recording… → Audio captured (sending)… and reports partial/final counts.

Acceptance checks

“Pipe alive” row goes green when asr_open and ≥1 audio frame observed.

Partial/final counters increment as expected during the test.

Phase 7 — Reliability, security, cost

Goal: production hardening.

Reliability

Queue limits: cap inbound queue (~1–2 s of audio); drop oldest on overflow.

Reconnect policy: if WS drops, bring UI back to Ready; don’t auto-resume streaming audio without a click.

Ping/pong: server ping ~25–30 s; client keep-alive ~4 s.

Security

Cookie session auth at WS handshake; Origin check; no CSRF needed on WS frames.

Redact PII in logs; rate-limit LLM, TTS, total audio seconds per session.

Cost

Track LLM tokens, TTS minutes, ASR minutes per session; enforce caps with clear UI.

Acceptance checks

Soak run (e.g., 2-hour session) without memory growth or audio stalls.

Idle tab for 10+ minutes: socket remains healthy with keep-alives (or re-establishes cleanly). 
developers.deepgram.com
+1

Phase 8 — Performance & UX polish

Goal: get “feels human” numbers.

Targets (starting points):

Interim time-to-first: ≤ ~800–1200 ms after speech start. 
developers.deepgram.com

Barge-in cut: ≤ ~150–250 ms to stop audio.

Final after close: ≤ ~8 s worst-case (tunable).

Tune: utterance_end_ms (1200–1500 ms), linger 600 ms, MediaRecorder timeslice 150–200 ms.

Acceptance checks

Meet or beat targets on wired/wifi; note that cellular jitter is higher.

Phase 9 — Deployment & runbook

Render

Web Service: ASGI via Gunicorn/Uvicorn; bind to $PORT; WS supported.

Start command unchanged; keep WEB_CONCURRENCY=1 initially; raise later if needed.

Health: add /api/v1/health (HTTP) to signal “ready.”

Runbook

Playbooks for: “no interims” (first blob/header or provider), “idle close” (keep-alive), “send_before_open” (lock), “partial floods” (tune utterance_end_ms).

Acceptance checks

Blue/green deploy succeeds; rollback tested.