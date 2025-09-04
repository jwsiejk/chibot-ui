# Ask Chip — Phase 4 (Real WebSocket, Admin Config, Rate Limits)

**This phase adds:**
- Real **WebSocket** endpoint on `/ws/v1/chat` (ASGI), while keeping HTTP v1 routes.
- In-memory **Admin Config** (GET/POST) + **SSE broadcast** on updates.
- Simple **rate limits** on `/api/v1/chat` and `/api/v1/voice/stt` (token-bucket style).
- Still 100% mocked vendors (LLM/STT/TTS/Email); no network calls.

Entrypoint stays `app.asgi_gateway:asgi` per the architecture.
