# OPERATING_INSTRUCTIONS (WS-only)

Audio only over `/ws/v1/chat`. No `/api/v1/voice/chunk` or `/api/v1/voice/end`.


## Phase 3 Runbook Notes
- WS KeepAlive: client may send {"type":"KeepAlive"} every ~15s; server replies with KeepAliveAck.
- Turn end: client sends {"type":"CloseStream"}; server replies with TurnClosed.
- Optional: {"type":"UtteranceEnd"} may be sent by the ASR; server will pass/ack.
- Security: OriginCheckMiddleware enforces allowed origins (configured in asgi_gateway.py).
- Usage caps: MAX_TURN_SEC, MAX_SESSION_MIN, RATE_LIMIT_PER_MIN envs are respected server-side (see app/config.py).
- Route-linter script: scripts/route_linter.py — excludes docs/tests, scans code only for legacy tokens.

## Phase 4 Runbook Notes
- Each user turn is tracked with `turn_id` (increments on CloseStream).
- Server emits normalized Deepgram-like frames:
  - `{"type":"Results","channel":{"alternatives":[{"transcript":""}]}, "is_final":true, "turn_id":N}`
  - `{"type":"UtteranceEnd","turn_id":N}`
- Tests run with mock ASR (no external network). In production, the real ASR path remains unchanged.
- TTS remains on `/api/v1/voice/tts-with-visemes` (HTTP), as permitted.

## Phase 5 Runbook Notes
- Outbound audio is chunked to ~8KB by default and sent on the session bus as `audio_chunk` frames.
- Cancelling a turn via `bus.cancel_turn(session_id, turn_id)` halts remaining chunks and suppresses the `assistant_end` frame for that turn.
- Production TTS continues to be served by `/api/v1/voice/tts-with-visemes`; the bus is used to stream chunks to the active WS lane.
