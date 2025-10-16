# Voice Pipeline Overview

This document summarizes the end-to-end voice capture and playback path inside
the AskChip UI, the primary modules that implement each stage, and the config
surface available to operators.

## Runtime Layout

```
static/js/voice/
├── core/        # DSP primitives and state machines (VAD, shadow buffers)
├── io/          # Audio IO helpers (mic capture, streams)
├── policy/      # Guardrail decisions (intent, toolplans)
├── ui/          # UI events exposed to the rest of the console
└── index.js     # Barrel export + config validation helpers
```

Supporting back-end code lives in:

```
app/services/streaming.py                    # Voice websocket handlers
app/services/streaming_asr/stream_manager.py # Deepgram listener + VAD taps
app/obs.py                                   # Structured logging to Admin feed
```

## Module Responsibilities

- **`static/js/voice/core`** – Implements the voice turn state machines.  The
  `HysteresisVAD`, `ShadowBuffer`, `EvidenceGate`, and `TurnState` classes work
  together to detect speech, maintain a pre-roll buffer, and decide when to
  commit a turn.
- **`static/js/voice/io`** – Hosts browser IO glue (mic access, media
  recorders).  The index exports will expand as legacy modules migrate.
- **`static/js/voice/ui`** – Emits DOM events (see `Events.js`) so diagnostics
  screens can react to state changes without reaching into internals.
- **`static/js/voice/index.js`** – Validates the voice config supplied by the
  server, normalising numeric ranges and boolean flags before the pipeline
  spins up.
- **`app/services/streaming_asr/stream_manager.py`** – Manages the Deepgram
  websocket connection, forwards frames into the VAD, and mirrors ASR events to
  the Admin log feed for observability.
- **`app/obs.py`** – Central logging hub.  Each `jlog` call appends to the admin
  diagnostics feed and mirrors important events to the websocket bus.

## Configuration Keys

Relevant defaults live in `static/js/voice/core/Config.js`.  Operators can tune
the following (values are validated in `static/js/voice/index.js`):

| Section          | Key                     | Description                                             |
|------------------|-------------------------|---------------------------------------------------------|
| `commit`         | `min_ms`                | Minimum duration (ms) required before committing audio. |
|                  | `no_partial_timeout_ms` | Timeout before force-committing without partials.       |
|                  | `drop_if_no_partial`    | Skip commit when no partial ASR result is seen.         |
| `tts`            | `decay_ms`              | How quickly to decay the “chip speaking” mask.          |
| `shadow`         | `ms`                    | Shadow buffer length to prepend when sending audio.     |
| `evidence`       | `snr_sigma`             | Noise threshold sigma for VAD evidence.                 |
|                  | `asr_conf`              | Minimum ASR confidence to treat a turn as valid.        |
|                  | `threshold`             | Evidence score threshold used by `EvidenceGate`.        |
| `metrics`        | `client_enabled`        | Enable client-side instrumentation.                     |
|                  | `server_enabled`        | Enable server-side instrumentation.                     |

Configuration updates made via `/api/v1/admin/config` emit `config_update`
events on the Admin log feed and take effect immediately.

## E2E Checklist

1. **Mic capture** – Confirm the browser grants permission and
   `startMicMeter` renders live levels.
2. **VAD arm/disarm** – Ensure the VAD arms after Chip finishes the greet turn
   and disarms when silence is detected or the operator presses Stop.
3. **ASR telemetry** – Watch the Admin log feed (now polled via
   `/api/v1/admin/logs`) for `asr:start`, `asr:partial`, and `asr:final` events
   tied to the active session ID.
4. **Turn commit** – Validate that partials/finals result in a `commit` frame on
   the websocket and that `EvidenceGate` metrics fall within expected ranges.
5. **Assistant playback** – Verify `assistant_audio` chunks or text frames flow
   back over the websocket after the ASR final.
6. **Admin observability** – Confirm manual diagnostics or UI actions emit
   structured entries to the Admin log feed for later triage.

Follow this checklist when modifying either the client VAD logic or the server
stream manager to ensure the voice lane remains healthy end-to-end.

