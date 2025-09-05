# Phase 1 — Acceptance Checklist

**Goal:** Audio playback & visemes in browser; real vendor wiring paths present; live apply of config/layout without reload.

## UI
- [ ] Client plays streamed `audio_chunk` messages (`codec: audio/webm;codecs=opus`) via a chunked player.
- [ ] Client animates visemes in sync with playback (driven by schedule messages or TTS response).
- [ ] Client listens for `config_updated` and `layout_updated` messages and applies them live (no page reload).

## Server
- [ ] `/api/v1/voice/tts-with-visemes` route exists and returns `{ok, audio_b64, visemes}`.
- [ ] Vendor wiring includes real providers (Whisper STT, ElevenLabs TTS) behind env toggles; default to mocks in tests.
- [ ] No external network is invoked during tests.

---

# Phase 2 — Acceptance Checklist

## Rate limits & one-WS-per-tab
- [ ] Requests to `/api/v1/chat` and `/api/v1/voice/stt` are rate-limited (429 on overflow).
- [ ] ASGI WS `/ws/v1/chat` rejects a second connection for the same (session_id, tab_id).

## Acceptance wiring (“email transcript on End”)
- [ ] `POST /api/v1/chat` with `{"cmd":"end_session"}` triggers transcript email (mocked) and returns `{ ok: true, emailed: true }`.

## Nudges & suggestions policy
- [ ] Server arms a deterministic nudge (~4200 ms by default) after `assistant_end` (or equivalent), with backoff/limit driven by config.
- [ ] Nudge is canceled on new user input (chat or voice).

---

# Phase 3 — Acceptance Checklist

## Barge-in (complete)
- [ ] WS supports soft barge-in: pause on barge_start, confirm after ~confirm_ms (default 420 ms), then commit (interrupt) unless canceled.
- [ ] 'interrupt' (ESC) commits immediately (no confirm delay).
- [ ] While paused, audio chunks are not forwarded to the client.

## Acceptance wiring (“email transcript on End”)
- [ ] `POST /api/v1/chat` with `{"cmd":"end_session"}` emails transcript (mock/record).

## Nudges & suggestions policy
- [ ] Nudge arming occurs deterministically after assistant_end, and is canceled by user input.

## Rate limits & “one WS per tab”
- [ ] Guard remains in place; acquiring the same (session_id, tab_id) twice fails until released.