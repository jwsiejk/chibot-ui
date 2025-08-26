
Ask Chip — Intelligence Pack (drop-in)

What’s included (updated/new):
- routes/chat.py — now orchestrates intent, memory & style. No echo; no lists.
- services/llm_service.py — adds generate_smart_response(...) and summarize_session(...).
- services/intents.py — lightweight intent+slot classifier (uses OpenAI JSON when available; regex fallback otherwise).
- memory.py — expanded with user_preferences, user_notes, session_summaries, and helpers.

DB changes (auto-created by memory.init_db):
- users(email, name, title, region)
- logs(id, email, role, message, created_at)
- session_summaries(email, summary, updated_at)
- user_preferences(email, tone, verbosity, channel, updated_at)
- user_notes(id, email, topic, note, weight, created_at) + FTS index
- feedback(id, email, session_id, message_id, rating, note, created_at)

How to deploy safely:
1) Replace the files in your app with the ones in this zip.
2) Ensure DATABASE_URL is set (Neon) and OPENAI_API_KEY is present.
3) On boot, call memory.init_db() once (app.py already calls it). Tables are created idempotently.
4) Verify the single canonical chat route is /api/chat (registered from routes/chat.py).

Quick tests:
- POST /api/chat {text: "portworx"} → Should ask “install, design, troubleshoot, or a quick briefing?”
- POST /api/chat {text: "FlashArray upgrade Purity 6.6 to 6.8"} → 30-word, no-list answer.
- Include channel hints: {text: "...", channel: "slack"} → Tighter formatting.

Security:
- Minimal data persisted (preferences, notes, summaries). PII is redacted in logs (emails/phones).
- No keys are hardcoded. Keep secrets in environment variables.
