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
