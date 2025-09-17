# Deploying Ask Chip on Render (Web Service)

## Build
pip install --upgrade pip
pip install -r requirements.txt

## Start
gunicorn -k uvicorn.workers.UvicornWorker -w ${WEB_CONCURRENCY:-1} --bind 0.0.0.0:$PORT app.asgi_gateway:asgi

## Notes
- Service type: Web Service (UI + API together).
- Render was created via Dashboard — all Environment Variables are set in the dashboard (no render.yaml).
- Keep WEB_CONCURRENCY=1 initially for WS stability; raise later if needed.
- Expose via $PORT only.
