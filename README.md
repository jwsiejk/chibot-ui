# Ask Chip — Full Build (Holistic)

End‑to‑end app with: login/profile, text + voice chat (≤30 words), Chip persona, short‑term memory, ElevenLabs TTS + visemes (with browser fallbacks), SMTP email, and Americas accounts CSV lookup.

## Render
- **Build:** `pip install -r requirements.txt`
- **Start:** `gunicorn -k gthread -w ${WEB_CONCURRENCY:-1} --threads ${WEB_THREADS:-8} --timeout 120 --bind 0.0.0.0:$PORT app:app`

## Slash commands
- `/team <account>` → looks up owner/type/region from CSV.

See `.env.example` for required env vars.

---
## Email (server-side SMTP)
- Endpoint: `POST /api/email/send`
- Env: `SMTP_HOST`, `SMTP_PORT` (587), `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM`
- Body: `{ "to": "...", "subject": "...", "body": "...", "html": "<optional>" }`

## Americas CSV account lookup
- File: `static/data/americas_accounts.csv` (override with `ACCOUNTS_CSV_PATH`)
- Endpoint: `GET /api/accounts/search?q=<substring>`
- Supports both schemas: `Account, Pure Rep, Pure Type` **and** `Account Name, Account Owner, Type`.
