# Speechmatics Realtime — Best Practices (BP)

HANDSHAKE ORDER (MUST)
- Send StartRecognition; wait for vendor **RecognitionStarted** before streaming any audio.
- Only after RecognitionStarted should the server emit **asr.ready** to the client.

ENDPOINT SELECTION (MUST)
- Endpoint selection uses a full URL via `SPEECHMATICS_REALTIME_URL` (default `wss://us1.rt.speechmatics.com/v2`).

AUDIO FORMAT (MUST MATCH BYTES)
- `audio_format: { type: "raw", encoding: "pcm_s16le", sample_rate: 16000 }`
- Binary frames are raw PCM; complete 16-bit little-endian samples; mono.
- The actual stream (rate/encoding/channels) must match the declared audio_format.

TRANSCRIPTION CONFIG (SHOULD)
- Explicitly set `transcription_config.language` (e.g., `"en"`) and `enable_partials: true`.
- Max final latency (`max_delay`) should be tuned between **0.7s and 4.0s**. We clamp below 0.7s to avoid vendor rejection.

CLIENT LIFECYCLE (MUST)
- Client starts mic **only** on `asr.ready`.
- If client arms early, it must not transmit until `asr.ready`.
- VAD activates exactly when `asr.ready` arrives; deactivate on mic stop.

CHUNKING (SHOULD)
- Batch ~**60–100ms** of PCM on the wire to reduce WS overhead while maintaining responsive UX.

KEEPALIVE (MUST)
- Rely on WebSocket **ping/pong** for keepalive; do not send vendor-specific KeepAlive frames.

ERRORING EARLY (MUST)
- If required open fields are missing/mismatched, **refuse to stream**; return structured error (no silent 0-partials).
