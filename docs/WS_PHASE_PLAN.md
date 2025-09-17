# WS-Everywhere Plan — Phase 0

**WS-only** migration groundwork.
Use `/ws/v1/chat` exclusively for audio.


## Phase 1 — WS Schema (Deepgram-aligned) ✅
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
