# Ask Chip Wiring Report

NOTE: Static scan; blueprint url_prefix may not be reflected (e.g., '/email/send' vs '/api/email/send').

## Frontend calls without backend (top 100)
- `POST` /api/voice/tts_with_visemes — static/chip/chip.js; static/js/api-smart-shim.js; static/js/api_smart-shim.js; static/user-experience/js/chat/send.js
- `GET` /api/me — static/js/addons-email-intents.js
- `GET` /api/profile — static/js/addons-email-intents.js
- `POST` /api/email/send — static/js/addons-email-intents.js
- `POST` /logout — static/user-experience/js/main.js
- `POST` /api/speak — static/user-experience/js/chat/send.js
- `POST` /api/chat/summary — static/user-experience/js/chat/send.js
- `POST` /api/chat — static/user-experience/js/chat/send.js
- `POST` /api/voice-once — static/user-experience/js/chat/send.js

## Backend routes with no frontend callers (top 100)
- `GET` / — app/legacy_app.py; routes/admin.py
- `GET` /admin/call-log — app/legacy_app.py
- `GET` /admin/stream — app/legacy_app.py
- `GET` /api/health — app/legacy_app.py
- `GET` /accounts/search — routes/accounts.py
- `GET` /stream — routes/admin.py
- `POST` /chat — routes/chat.py
- `GET` /conversation — routes/conversation.py; routes/legacy_block.py
- `OPTIONS` /conversation — routes/conversation.py; routes/legacy_block.py
- `POST` /conversation — routes/conversation.py; routes/legacy_block.py
- `GET` /orchestrator — routes/conversation.py; routes/legacy_block.py
- `OPTIONS` /orchestrator — routes/conversation.py; routes/legacy_block.py
- `POST` /orchestrator — routes/conversation.py; routes/legacy_block.py
- `POST` /email/send — routes/email_api.py
- `GET` /greet — routes/greet.py
- `GET` /profile — routes/profile.py
- `POST` /profile — routes/profile.py
- `GET` /askchip-diagnostics.html — routes/tools.py
- `GET` /admin-log.html — routes/tools.py
- `GET` /health — routes/voice.py