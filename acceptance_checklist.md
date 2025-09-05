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