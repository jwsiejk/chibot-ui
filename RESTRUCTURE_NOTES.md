# Restructure Notes

This archive contains a cleaned, modularized structure for **Ask Chip**.

## Highlights
- All request handlers are now Blueprints under `routes/`:
  - `routes/conversation.py` now serves `POST /api/chat` (renamed from `/api/chat_orchestrated`).
  - `routes/greet.py` serves `GET/POST /api/greet`.
  - `routes/voice.py` registers `voice_bp` with `/api/voice/*` endpoints.
- `app.py` is now the **application shell only**:
  - Registers `conversation`, `greet`, and `voice` blueprints.
  - Keeps `/api/tts` and `/api/tts_with_visemes` endpoints using `services.tts_service`.
  - Removes duplicate inlined routes for `/api/greet` and `/api/chat`.
- No business logic lives in routes; services remain in `services/`:
  - `llm_service.py` (Chip persona + replies)
  - `tts_service.py` (ElevenLabs)
  - `email_service.py`, `accounts_service.py`
  - `memory.py` (DB access) left top-level for backward compatibility; import path unchanged in services/routes.
- Static assets are preserved in `static/`, including the `chip/` visemes and `user-experience/js/*` modules.
- WSGI entry remains `wsgi.py` (imports `app:app`). Render/Procfile unchanged.

## Run
```
pip install -r requirements.txt
python app.py
# open http://localhost:5000
```

## Notes
- If any client code previously called `/api/chat_orchestrated`, update it to `/api/chat`.
- `routes/chat.py` is present (from prior draft API) but **not registered**; keep it for reference/migration.
- Security: No secrets added. `.env.example` only; ensure your real `DATABASE_URL`, `OPENAI_API_KEY`, and ElevenLabs keys are provided via env vars in Render. `memory.py` keeps `sslmode=require`.