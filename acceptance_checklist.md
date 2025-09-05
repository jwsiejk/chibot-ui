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

---

# Phase 4 — Acceptance Checklist

## Neon persistence
- [ ] With DATABASE_URL set (sqlite in tests), config, layouts, profiles, sessions/messages persist across a reload.
- [ ] db layer reads/writes through DAL; changes survive reset.

## Admin – Users & Memory
- [ ] Endpoints list/search users; list sessions; get session detail; export/email transcript; anonymize/delete.

## Admin – Layout editor (advanced)
- [ ] Publish per-breakpoint layouts with version increment.
- [ ] List versions and rollback to a prior version.

## Admin – Config (full surface)
- [ ] Config writes are versioned and persisted; `config_updated` events fire to subscribers.

## Profile gate / Auth
- [ ] First-time profile detection and save path work via API; persisted in DB.

---

# Phase 5 — Acceptance Checklist

## Admin Log (SSE)
- [ ] GET /api/v1/admin/logs streams newline-delimited JSON events.
- [ ] Key actions emit events: greet, stt, tts, ws_open/close, nudge, interrupt, config_update, layout_publish.

## Real vendor lanes (prod)
- [ ] Providers implement real paths guarded by env: Whisper STT (language lock, normalization), ElevenLabs TTS (with visemes).
- [ ] Tests use mocks (no external network).

## Route-linter
- [ ] Tests fail if any non-v1 route like '/api/greet' exists or legacy symbols are present.

## Profile gate UI
- [ ] index.html (or base template) includes a disabled Start button and a small script that calls /api/v1/profile/get and enables Start when exists=true.