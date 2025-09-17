# Environment Variables (canonical names)

Identity/Admin
- ADMIN_EMAILS — comma‑separated admin emails

Database
- DATABASE_URL — Neon Postgres DSN (pooler host; sslmode=require)

OpenAI
- OPENAI_API_KEY — API key (LLM + Whisper)
- OPENAI_MODEL — (optional) model override
- OPENAI_STT_LANGUAGE — (optional, default 'en')

ElevenLabs (TTS)
- ELEVENLABS_API_KEY
- ELEVENLABS_VOICE_ID
- ELEVEN_MODEL_ID (optional)
- ELEVEN_OUTPUT_FORMAT (e.g., mp3_44100_128)
- ELEVEN_OUTPUT_FORMAT_WS (optional)
- ELEVEN_INACTIVITY (optional)

Email / SMTP
- EMAIL_HOST
- EMAIL_PORT
- EMAIL_USE_TLS
- EMAIL_HOST_USER
- EMAIL_HOST_PASSWORD
- EMAIL_FROM_NAME
- FROM_EMAIL
- EMAIL_WEBHOOK_URL (optional)
- EMAIL_WEBHOOK_SECRET (optional)

Feature Toggles
- FEATURE_ADMIN_UI
- FEATURE_AUDIO
- FEATURE_TOOLS

Core App
- SECRET_KEY
- SESSION_TYPE
- PIP_ONLY_BINARY
