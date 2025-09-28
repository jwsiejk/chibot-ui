# Environment Variables (Phase 8)
SECRET_KEY=change-me
ADMIN_EMAILS=james@example.com
DATABASE_URL=postgresql://...  (unused in tests; DAL is in-memory mock)
WS_PING_INTERVAL_MS=25000
# Raw fallback is temporarily disabled by default; set the FORCE flag below to
# opt back in while the kill-switch remains in place.
CHIBOT_DISABLE_RAW_FALLBACK=0  # Legacy kill switch; truthy to disable the Deepgram raw PCM fallback heuristics.
CHIBOT_FORCE_ENABLE_RAW_FALLBACK=0  # Set to 1/true to re-enable raw fallback heuristics (default is temporarily off).
