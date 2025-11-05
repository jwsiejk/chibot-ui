# AskChip Voice — Golden Path Invariants (INVARS)

TURN CONTRACT (must always hold)
1) Vendor handshake: client/server send StartRecognition → vendor replies **RecognitionStarted**.
2) Server emits **asr.ready** only **after** RecognitionStarted.
3) Client starts mic **only** on asr.ready (single start per turn).
4) Header: exactly once per recorder session; identical duplicate is **ignored** by server; conflicting header is **error** and stream stops.
5) Chunks flow only between asr.ready and first final; mic **stops on first final** (then cooldown 1.5 s).
6) Recorder does **not** restart mid-turn; use pause/resume (mask) during TTS; new sessionId only when we truly stop.

AUDIO CONTRACT
- Encoding: **pcm_s16le**, **16,000 Hz**, mono; frames = raw PCM bytes (complete 16-bit samples, little-endian).
- Open config includes:
  - `audio_format: { type: "raw", encoding: "pcm_s16le", sample_rate: 16000 }`
  - `transcription_config: { language: "en", enable_partials: true }`
- Client capture path is truly 16 kHz; if device runs 48 kHz, client **downsamples to 16 kHz** before send.

SERVER ORDERING & IDENTITY
- Only one vendor stream **open** per SID at a time; second open rejected with `code="asr_open_busy"`.
- **RecognitionStarted** is the only vendor readiness signal; do not treat `Info/AudioAdded` as ready.
- `asr.ready` is emitted exactly once per stream open.

HEADER POLICY
- Server stores last accepted header `{codec, rate_hz, channels}` per stream.
- **Identical** header mid-stream: no-op (log `evt=audio_header_dup_ignored`).
- **Conflicting** header mid-stream: error `code="header_conflict"`; terminate stream cleanly.

UI/STATE CONTRACT
- UI shows **Listening** only after asr.ready; VAD becomes active at that exact moment.
- State machine: Disconnected → Connecting → Greeting → Listening → (Thinking →) Responding → Listening → …
- Exactly one `mic_start` and one `mic_stop` log per turn.

LOGGING (single-line, key=value; must appear in zip)
- Server: `evt=sm_recognition_started`, `evt=audio_dropped_before_asr_open` (if hit), `evt=audio_header_dup_ignored` (if hit), `asr_rollup partials=<n> finals=<n> bytes=<n>`.
- Client: `client.mic evt=mic_start|mic_stop`, `fsm.transition prev=... next=...`, `ui.badge label="Listening"` when it flips.

PROHIBITED (block merge if detected)
- Mic start from any path other than the **asr.ready** handler.
- Emitting **asr.ready** before **RecognitionStarted**.
- Recorder stop/restart mid-turn (except on first final or explicit teardown).
- Sending any non-PCM s16le audio or misdeclaring the `{codec,rate_hz,channels}` descriptor.
