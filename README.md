# Ask Chip — Starter Repo (Phase 0 ready)

This is a clean skeleton matching the architecture spec. Endpoints and WS are **stubbed** to let tests pass in Phase 0. 
Subsequent phases will implement real logic with tests-first.

## Run (dev)
```
pip install -r requirements.txt
gunicorn -k uvicorn.workers.UvicornWorker -w ${WEB_CONCURRENCY:-1} --bind 0.0.0.0:8000 app.asgi_gateway:asgi
```
