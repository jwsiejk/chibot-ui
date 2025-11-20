# Chibot UI

This project contains the web client for the AskChip experience.

## Recorder Policy

```
/**
 * POLICY: The PCM s16le capture pipeline lives entirely inside the websocket client.
 * Legacy browser recording primitives and send-gate helpers have been removed.
 * No manual or VAD-based barge-in; wake-word only.
 */
```

## Firehose Logging (FIREHOSE_LOGS)

Enable Firehose logging by setting `FIREHOSE_LOGS=true` in the environment. When adding new logs, route all client events through `logStage(label, detail)`, `logMic(detail)`, or `recordClientBannerEvent(label, meta)`. For server-side events, use `_emit_session_step(...)` for timeline steps, `_emit_hub_log(...)` for client-lane events, and `logging.*(..., extra={"sid": ..., "event": ...})` for standard entries. Logs are visible in `/admin/logs` and in exported flow ZIPs from the Admin Logs UI.
