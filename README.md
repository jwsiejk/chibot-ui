# Chibot UI

This project contains the web client for the AskChip experience.

## Recorder Policy

```
/**
 * POLICY: MediaRecorder may ONLY be instantiated in app/static/js/audio_recorder.js.
 * AudioRecorder is the single owner of the mic and the send-gate.
 * No manual or VAD-based barge-in; wake-word only.
 */
```
