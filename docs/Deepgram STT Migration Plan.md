# Deepgram STT Migration Plan (AskChip)

## Status

**Deepgram is now the sole streaming STT provider.** The Google Cloud Speech-to-Text implementation, configuration, and dependencies have been fully removed. There is no vendor failover or shadow mode.

## Current STT Architecture (Deepgram Only)

### Core flow
- **WebSocket adapter:** `app/ws/adapter.py` instantiates `DeepgramStreamingASREngine` and manages ASR open/close lifecycle.
- **ASR engine implementation:** `app/services/asr/deepgram_engine.py` handles Deepgram streaming and emits `asr.partial` / `asr.final` events.
- **Client audio ingress:** `app/static/js/audio/ws_audio_runtime.js` sends PCM16 audio frames over `/ws/v2/chat`.
- **Transcript UI bridge:** `app/static/js/ws/transcript_bridge.js` consumes `asr.partial` / `asr.final` events.

### Supported configuration
- `DEEPGRAM_API_KEY`
- `DEEPGRAM_STT_MODEL` (default: `nova-2`)
- `DEEPGRAM_STT_LANGUAGE` (default: `en-US`)
- `DEEPGRAM_STT_SAMPLE_RATE` (default: `16000`)
- `DEEPGRAM_STT_ENDPOINTING_MS`
- `DEEPGRAM_STT_INTERIM_RESULTS`

### Behavior notes
- PCM16, 16kHz, mono remains the enforced audio path.
- `asr.partial` and `asr.final` event shapes remain unchanged for client compatibility.
- No legacy STT fallback, shadowing, or vendor selection logic exists.
