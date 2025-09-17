# WS_PHASE_PLAN — Summary (Phase 0 excerpt)

**Objective:** Establish v1-only API surface and WS lane at `/ws/v1/chat`. Remove HTTP chunk/end endpoints and set guardrails.

## Phase 0 Acceptance
- Entrypoint `app.asgi_gateway:asgi` exists and mounts Flask + Starlette.
- Routes present: `/api/v1/greet`, `/api/v1/chat`, `/api/v1/voice/stt`, `/api/v1/voice/tts-with-visemes`, `/api/v1/admin/logs`, `/ws/v1/chat`.
- Route-linter fails on `/api/greet` or legacy symbols (`legacy_app`, `sendChat(`).

## Notes
- Audio will be moved to WebSocket binary frames in Phase 1/2.
- ElevenLabs TTS keeps `/api/v1/voice/tts-with-visemes` (WS TTS may arrive later).