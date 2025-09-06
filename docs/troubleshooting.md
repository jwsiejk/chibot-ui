# Troubleshooting
- **Database errors:** Check /api/v1/admin/db/health. If failing, verify DATABASE_URL, retry/backoff settings.
- **No transcripts:** Inspect /api/v1/admin/outbox; check last_error and attempts; ensure SMTP env vars.
- **High latency:** See metrics (askchip.*). Investigate LLM/STT/TTS timings and WS disconnects.
- **Config bad state:** Use /api/v1/admin/config/rollback to revert to prior version.
