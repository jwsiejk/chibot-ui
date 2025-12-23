# BUILD 09 — QA / Acceptance Script (non-code)

## Context
Everything above is wired: policies, env gates, vendor selection/logging, Deepgram STT client, PCM recorder, validation, RMS probe, endpointing, and telemetry.

## Objective
Validate the conversational flow when using the PCM16 audio pipeline with Deepgram STT without requiring a redeploy.

## Preconditions
- Admin console is accessible.
- Live Tail and Zip log collectors are available.
- Speech client hardware and microphone are functional.

## Test Matrix

### Deepgram STT / PCM16 Happy Path
1. In **Admin**, confirm **ASR Vendor** is `deepgram` and **Audio Pipeline** is `pcm16`.
2. Start a fresh session.
3. Speak a single sentence, then stop.
4. Confirm the following events appear (in order of occurrence) in both Live Tail and the Zip export:
   - `asr_vendor_selected primary=deepgram ...`
   - `sm_ws_open encoding=linear16 sr=16000 ch=1 interim=true lang=en`
   - `asr_probe rms_avg>0.01`
   - `asr_partial ...` within **300–800 ms** of speech onset.
   - `client.asr evt=commit reason=vad_silence dur_ms≈900`
   - `asr_final ...`, followed by a TTS reply.
   - `asr_rollup vendor=deepgram partials>0 finals>=1 bytes>0`

### Negative Scenarios
1. **Talk over TTS response**
   - While TTS is playing, talk continuously.
   - Verify that microphone frames are masked (no frames forwarded to ASR).
2. **Extended utterance (>8 s)**
   - Speak for longer than eight seconds.
   - Confirm the capture is capped and a final ASR event is emitted automatically.

## Acceptance Criteria
- All expected log lines appear in both Live Tail and the Zip archive for each scenario.
