# Firehose Logging (FIREHOSE_LOGS)

Set `FIREHOSE_LOGS=true` to enable the firehose. Client logs must go through `logStage(label, detail)`, `logMic(detail)`, or `recordClientBannerEvent(label, meta)`. Server logs should use `_emit_session_step(...)` for timeline updates, `_emit_hub_log(...)` for client-lane events, and `logging.*(..., extra={"sid": ..., "event": ...})` for routine messages. View collected logs in `/admin/logs` or by downloading the exported flow ZIP from the Admin Logs UI.
