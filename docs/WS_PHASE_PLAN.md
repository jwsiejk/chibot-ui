# WS-Everywhere Plan — Phase 0 — **COMPLETE**

**WS-only** migration groundwork.
Use `/ws/v1/chat` exclusively for audio.


## Phase 1 — **COMPLETE** — WS Schema (Deepgram-aligned) ✅
**Goal:** Implement the client/server message types on `/ws/v1/chat` without vendor calls.

### Client → Server
- Binary audio frames (mic)
- `{"type":"KeepAlive"}`
- `{"type":"CloseStream"}`

### Server → Client
- `{"type":"Results","channel":{"alternatives":[{"transcript":""}]}, "is_final":true, "turn_id":N}`
- `{"type":"UtteranceEnd","turn_id":N}` (optional, emitted here)
- `{"type":"KeepAliveAck"}`

### Acceptance (tests)
- Unit: schema helpers produce correct shapes.
- Unit: turn buffer collects bytes, increments turn_id on CloseStream.
- Static check: `app/ws/ws_asgi.py` references `KeepAlive`, `CloseStream`, `UtteranceEnd`, and `Results`.
- Route-linter still passes; no banned routes/symbols; `/ws/v1/chat` present.


## Phase 2 — Deepgram Wiring (pass-through) — **COMPLETE**
- WS handler opens Deepgram WS on first binary frame.
- Forwards bytes via `DeepgramClient.send()`.
- Pass-through of Deepgram `Results` and `UtteranceEnd` messages to client.
- `KeepAlive` → `KeepAliveAck`.
- **Removed** HTTP `/api/v1/voice/chunk` and `/api/v1/voice/end` endpoints.


## Phase 3 — Control, Security, Lints — **COMPLETE**
- KeepAlive → KeepAliveAck
- CloseStream handled (turn end)
- Optional UtteranceEnd passthrough
- Origin check middleware present
- PII redaction helper present
- Usage caps present
- Route-linter blocks legacy routes

## Phase 4 — Results Normalization & Turn IDs — **COMPLETE**
**Goal:** Normalize ASR events to Deepgram-like Results frames with turn tracking.
- Each user turn (between mic start and CloseStream) has a monotonically increasing `turn_id`.
- Server emits `{"type":"Results","channel":{"alternatives":[{"transcript":""}]}, "is_final":bool, "turn_id":N}`.
- Server emits `{"type":"UtteranceEnd","turn_id":N}` at the end of a turn.
- Tests mock the ASR vendor; no external network calls.
- TTS remains HTTP /api/v1/voice/tts-with-visemes.

### Acceptance (tests)
- Unit: `make_results()` includes `channel.alternatives[0].transcript` and top-level `is_final` + `turn_id`.
- Unit: ws handler increments `turn_id` on successive `CloseStream`s.
- Unit: with mock ASR enabled, sending one binary frame then `CloseStream` yields a `Results` and `UtteranceEnd` with matching `turn_id`.
- Lint: route-linter passes; no forbidden routes.
