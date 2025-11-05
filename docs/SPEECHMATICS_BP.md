# Speechmatics Realtime — Best Practices (BP)

HANDSHAKE ORDER (MUST)
- Send StartRecognition; wait for vendor **RecognitionStarted** before streaming any audio.
- Only after RecognitionStarted should the server emit **asr.ready** to the client.

AUDIO FORMAT (MUST MATCH BYTES)
- `audio_format: { type: "raw", encoding: "pcm_s16le", sample_rate: 16000 }`
- Binary frames are raw PCM; complete 16-bit little-endian samples; mono.
- The actual stream (rate/encoding/channels) must match the declared audio_format.

TRANSCRIPTION CONFIG (SHOULD)
- Explicitly set `transcription_config.language` (e.g., `"en"`) and `enable_partials: true`.

CLIENT LIFECYCLE (MUST)
- Client starts mic **only** on `asr.ready`.
- If client arms early, it must not transmit until `asr.ready`.
- VAD activates exactly when `asr.ready` arrives; deactivate on mic stop.

CHUNKING (SHOULD)
- 50–100 ms PCM16 chunks (≈1.6–3.2 KB) balance latency and overhead.

ERRORING EARLY (MUST)
- If required open fields are missing/mismatched, **refuse to stream**; return structured error (no silent 0-partials).
