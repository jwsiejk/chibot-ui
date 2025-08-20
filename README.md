# Ask Chip — Complete Build

Features: profile gating, Chip persona (≤30 words), conversation memory, mic voice I/O, ElevenLabs TTS (server-side) with browser fallback, visemes, email send API, and Americas CSV account lookup.

## Run Locally
```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export FLASK_ENV=development
python app.py
# open http://localhost:5000
```

## Render
Build: `pip install -U pip setuptools wheel && pip install --prefer-binary -r requirements.txt`  
Start: `gunicorn -k gthread -w ${WEB_CONCURRENCY:-1} --threads ${WEB_THREADS:-8} --timeout 120 --graceful-timeout 30 --keep-alive 15 --bind 0.0.0.0:$PORT app:app`

## Endpoints
- `GET /api/me` — auth state
- `POST /api/login` — set session email
- `POST /api/logout`
- `GET|POST /api/profile`
- `GET /api/greet`
- `POST /api/chat` — Chip reply (≤30 words, with memory & profile context)
- `POST /api/tts` — audio/mpeg from ElevenLabs (fallback to browser speech on client)
- `POST /api/tts_with_visemes` — `{ audio(base64|null), visemes[], relative:true }`
- `POST /api/email/send` — server-side SMTP
- `GET /api/accounts/search?q=...` — CSV lookup
```

