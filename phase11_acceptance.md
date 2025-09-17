# Phase 11 — Open Items Confirmed

- **Render WS keep-alive/idle** exposed as config (`ws_ping_interval_ms`, `ws_idle_timeout_ms`) and visible via `/api/v1/admin/diagnostics`. Live updates broadcast on `config_updated` SSE.
- **Browser/codec grid** served at `/api/v1/admin/compat/codecs` (GET/PUT) with version bump; Safari caveats tracked.
- **AudioWorklet migration plan** returned by `/api/v1/admin/audio_migration`, reflecting current vs target modes with milestone checklist.
- All routes **Admin-gated** via `ADMIN_EMAILS`; audited via Admin Log.
- v1-only; WS-only chat; no vendor calls.
