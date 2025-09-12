# Ask Chip — Production Health Endpoint (Drop-In)

This bundle is tailored to your repo: v1-only surfaces with blueprint registration in `app/asgi_gateway.py`.

## Files
- `app/api_v1/health.py` — new v1 health blueprint (`GET /api/v1/health`)
- `app/asgi_gateway.py` — updated to register the health blueprint and quietly 204 on `HEAD /`
- `tests/test_health.py` — pytest ensuring `/api/v1/health` returns `{ "ok": true }`

## Deploy
1. Paste files into your repo at the same paths (overwrite `app/asgi_gateway.py`).
2. Deploy with your existing start command:
   `gunicorn -k uvicorn.workers.UvicornWorker -w ${WEB_CONCURRENCY:-1} --bind 0.0.0.0:$PORT app.asgi_gateway:asgi`
3. In Render, set Health Check Path to `/api/v1/health`.

That's it. No DB changes required for this patch.
