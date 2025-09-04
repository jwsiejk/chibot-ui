# Deploy to Render (WS + Flask, no render.yaml)
- Create a **Web Service** on Render.
- Entrypoint: `app.asgi_gateway:asgi` (Gunicorn/Uvicorn).
- Set env vars (see env_vars.md).
- Health Check: `GET /api/v1/admin/config` should return 200.
- No outbound networking required for tests (vendors mocked).
