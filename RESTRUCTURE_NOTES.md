# Ask Chip — Restructure v2 (Complete)

**What’s done now**

1) **Voice endpoints consolidated**
   - New: `routes/voice.py` with `/api/voice/tts` and `/api/voice/tts_with_visemes`.
   - Removed inline `/api/tts*` routes from `app.py`.
   - Front-end and docs updated to use `/api/voice/*`.

2) **Chat endpoint stabilized**
   - New: `routes/chat.py` registered at `POST /api/chat` (DI-friendly).
   - Back-compat: `routes/conversation.py` provides `POST /api/chat_orchestrated` if needed.
   - Removed registration of legacy `conversation_orchestrator` to prevent logic conflicts.

3) **Loop + greeting bleed fix**
   - Eliminated the "I don’t have any history yet to email." path.
   - Email drafting is **opt-in only** (explicit `mode='email'` or user asks to “email/draft”). 
   - Greeting-style prompts no longer leak into normal chat.

4) **Docs & tree**
   - This file and `RESTRUCTURE_TREE.txt` updated.
   - Minimal `generate_response()` wrapper added to `services/llm_service.py` so routes have a stable entrypoint.

**Security** (per your standing requirement)
- No secrets in repo; `.env.example` only.
- DB still uses `sslmode=require` (see `memory.py`).
- No sample customer data shipped.

**Endpoints**

- `POST /api/chat` → primary chat
- `POST /api/chat_orchestrated` → compatibility (not recommended)
- `POST /api/voice/tts` → base64 audio
- `POST /api/voice/tts_with_visemes` → base64 audio + visemes
- `GET/POST /api/greet` → greeting