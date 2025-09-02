# Ask Chip Wiring Report

NOTE: Static scan; blueprint url_prefix may not be reflected (e.g., '/email/send' vs '/api/email/send').

## Frontend calls without backend (top 100)
- `POST` /api/voice/tts_with_visemes — static\chip\chip.js; static\js\api-smart-shim.js; static\js\api_smart-shim.js
- `GET` /api/profile — static\js\addons-email-intents.js
- `POST` /api/email/send — static\js\addons-email-intents.js
- `POST` /api/login — static\user-experience\js\auth\profile.js
- `POST` /api/profile — static\user-experience\js\auth\profile.js
- `POST` /api/v1/voice/tts-with-visemes — static\user-experience\js\chat\send.js
- `POST` /api/v1/chat — static\user-experience\js\chat\send.js
- `POST` /api/v1/voice/stt — static\user-experience\js\chat\send.js

## Backend routes with no frontend callers (top 100)
- `GET` / — app\legacy_app.py; routes\admin.py
- `GET` /healthz — app\legacy_app.py
- `GET` /health — app\legacy_app.py; routes\voice.py
- `GET` /api/health — app\legacy_app.py
- `GET` /api/voice/health — app\legacy_app.py
- `GET` /favicon.ico — app\legacy_app.py
- `GET` /accounts/search — routes\accounts.py
- `GET` /stream — routes\admin.py
- `POST` /login — routes\auth.py
- `POST` /chat — routes\chat.py
- `POST` /email/send — routes\email_api.py
- `GET` /greet — routes\greet.py
- `GET` /profile — routes\profile.py
- `POST` /profile — routes\profile.py
- `GET` /askchip-diagnostics.html — routes\tools.py
- `GET` /admin-log.html — routes\tools.py