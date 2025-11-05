# BUILD 09 — QA / Acceptance Script (non-code)

## Context
Everything above is wired: policies, env gates, vendor selection/logging, Speechmatics client, PCM recorder, validation, RMS probe, endpointing, and telemetry.

## Objective
Validate the conversational flow when using the PCM16 audio pipeline across both Speechmatics and Deepgram without requiring a redeploy.

## Preconditions
- Admin console is accessible.
- Live Tail and Zip log collectors are available.
- Speech client hardware and microphone are functional.

## Test Matrix

### Speechmatics / PCM16 Happy Path
1. In **Admin**, set **ASR Vendor** to `speechmatics` and **Audio Pipeline** to `pcm16`.
2. Start a fresh session.
3. Speak a single sentence, then stop.
4. Confirm the following events appear (in order of occurrence) in both Live Tail and the Zip export:
   - `asr_vendor_selected primary=speechmatics ...`
   - `sm_ws_open encoding=linear16 sr=16000 ch=1 interim=true lang=en`
   - `asr_probe rms_avg>0.01`
   - `asr_partial ...` within **300–800 ms** of speech onset.
   - `client.asr evt=commit reason=vad_silence dur_ms≈900`
   - `asr_final ...`, followed by a TTS reply.
   - `asr_rollup vendor=speechmatics partials>0 finals>=1 bytes>0`

### Negative Scenarios
1. **Talk over TTS response**
   - While TTS is playing, talk continuously.
   - Verify that microphone frames are masked (no frames forwarded to ASR).
2. **Extended utterance (>8 s)**
   - Speak for longer than eight seconds.
   - Confirm the capture is capped and a final ASR event is emitted automatically.

### Deepgram / PCM16 Parity
1. In **Admin**, switch **ASR Vendor** to `deepgram` (pipeline remains `pcm16`).
2. Initiate a new session and perform a short utterance.
3. Verify the Deepgram logging path mirrors the PCM16 telemetry expectations (partials within target latency, `asr_final`, `asr_rollup` counts, etc.).

## Acceptance Criteria
- All expected log lines appear in both Live Tail and the Zip archive for each scenario.
- Switching between Speechmatics/PCM16 and Deepgram/PCM16 works seamlessly within the same deployment.
