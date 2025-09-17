# OPERATING INSTRUCTIONS — Phase 0 (WS‑only groundwork)

This repo is prepared for Render deployment with ASGI (Gunicorn → UvicornWorker) and v1‑only surfaces.

## How to run (Render)
- **Build:** `pip install --upgrade pip && pip install -r requirements.txt`
- **Start:** `gunicorn -k uvicorn.workers.UvicornWorker -w ${WEB_CONCURRENCY:-1} --bind 0.0.0.0:$PORT app.asgi_gateway:asgi`

Keep `WEB_CONCURRENCY=1` initially for WebSocket stability. All traffic binds to `$PORT`.
(Ref: deploy instructions.)

## Surfaces (v1 only)
- `GET /api/v1/greet`
- `POST /api/v1/chat`
- `POST /api/v1/voice/stt` (presence only; WS lane will carry mic in later phases)
- `POST /api/v1/voice/tts-with-visemes` (ElevenLabs integration; mocked in tests)
- `GET /api/v1/admin/logs` (SSE for Admin Log)
- `WS /ws/v1/chat` (native WS via Starlette) — **audio will move to this lane in Phase 1+**

### Guardrails
- No legacy routes (route‑linter fails on `/api/greet`, `legacy_app`, `sendChat(`).
- No `/api/v1/voice/chunk` or `/api/v1/voice/end` endpoints exist.
- One WS per tab policy will be enforced in later phases.

## Data Analysis — How to test locally
Use ChatGPT **Data Analysis** to run tests and build zips. See *how_to_use_data_analysis.md*.
(Ref doc attached in this chat.)

## Environment
See **docs/ENV_VARS.md** for canonical environment variables.