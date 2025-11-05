# Chibot UI

This project contains the web client for the AskChip experience.

## Recorder Policy

```
/**
 * POLICY: The PCM s16le capture pipeline lives entirely in app/static/js/audio_recorder.js.
 * AudioRecorder is the sole owner of MediaRecorder, the microphone stream, and the send-gate.
 * No manual or VAD-based barge-in; wake-word only.
 */
```
