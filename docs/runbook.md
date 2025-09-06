# Ask Chip — Runbook (Phase 21)
## Procedures
- **Rotate keys:** Update Render env vars (OPENAI_API_KEY, ELEVENLABS_API_KEY, SMTP). Restart service.
- **Trip breakers:** Disable audio by setting FEATURE_AUDIO=false; restart.
- **Rollback deploy:** Point Render to previous repo zip branch; redeploy.
- **Disable audio quickly:** FEATURE_AUDIO=false; also raise nudge_delay_ms to avoid mic loops.
- **Purge outbox queue:** Use /api/v1/admin/outbox; remove stuck items or let retries proceed.
## Health Checklist
- /api/v1/admin/db/health == OK
- Admin Logs streaming
- WS open/keepalive steady
- Transcript emails flowing
## Contacts
- Owner: James (ADMIN_EMAILS).

