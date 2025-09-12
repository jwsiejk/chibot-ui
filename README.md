# Ask Chip — Phase 4 (Real WebSocket, Admin Config, Rate Limits)

**This phase adds:**
- Real **WebSocket** endpoint on `/ws/v1/chat` (ASGI), while keeping HTTP v1 routes.
- In-memory **Admin Config** (GET/POST) + **SSE broadcast** on updates.
- Simple **rate limits** on `/api/v1/chat` and `/api/v1/voice/stt` (token-bucket style).
- Still 100% mocked vendors (LLM/STT/TTS/Email); no network calls.

Entrypoint stays `app.asgi_gateway:asgi` per the architecture.

## Production Provider Policy (No Mocks)
- The app will **refuse to boot with mock providers** unless `CI_FAST` is set.
- LLM requires `OPENAI_API_KEY` (set `OPENAI_MODEL` optionally).
- TTS requires `ELEVENLABS_API_KEY` (+ `ELEVENLABS_VOICE_ID`).
