# WebSocket Contract — `chat.v2`

**Endpoint:** `/ws/v2/chat` (ASGI WS)  
**Required subprotocol:** `chat.v2`  

---

## Handshake & Authentication (browser-safe)

- **Single path & protocol:** only `/ws/v2/chat` with `Sec-WebSocket-Protocol: chat.v2`.
- **Authentication:** bearer tokens are no longer required. Any `Authorization` header or `access_token` query parameter is ignored. Restrict access using network controls (VPN, IP allow-lists) if needed.
- **Version negotiation:** if the subprotocol is missing/mismatched, the server replies **426** with a JSON body `{ "code": "bad_subprotocol" }`.

### Security

- **Origin enforcement:** this build does **not** enforce the `Origin` header during the WebSocket handshake. Deployments should rely on external controls (reverse proxies, firewalls) if Origin restrictions are required.

On successful upgrade, the server emits an initial `info` frame with connection metadata (see “Initial `info` frame”).

---

## Client → Server frames

- `{"type":"ping"}`
- `{"type":"client.ready"}`

- **Audio (binary frames):**  
  The **server policy** selects the ASR provider and **required input audio**. Clients **MUST** follow the descriptor the server announces (see `asr.ready`). The client **MUST NOT** change codecs/containers; format is controlled by policy (Deepgram primary, Speechmatics secondary).  

  - **Primary (Deepgram):** **WebM containerized Opus**, mono, **48 kHz**.  
    The browser typically produces this via `MediaRecorder('audio/webm;codecs=opus')`.  
  - **Secondary (Speechmatics):** **RAW PCM s16le**, mono, **16 kHz**.

  The server signals readiness and the required input descriptor via:

  ```json
  {
    "type": "asr.ready",
    "vendor": "deepgram",
    "input": { "container": "webm", "codec": "opus", "rate_hz": 48000, "channels": 1 }
  }
or

json
Copy code
{
  "type": "asr.ready",
  "vendor": "speechmatics",
  "input": { "container": "raw", "codec": "pcm_s16le", "rate_hz": 16000, "channels": 1 }
}
Binary message rule: each WS binary message carries an arbitrary contiguous chunk of the current input stream (WebM segment/cluster bytes for Opus, or a raw PCM chunk). The server assigns/validates sequencing (see “Binary audio sequencing & jitter buffer”).

audio.header (optional, not for selecting codecs):
Clients may not override format. audio.header can supply stream hints like a starting sequence index:

json
Copy code
{"type":"audio.header","seq_start":0}
Optional admin toggle:

json
Copy code
{"type":"admin.toggle","barge_in_enabled":true}
Provider selection is policy‑controlled; do not flip providers via WS.

Optional resume handshake (see “Reconnect / Resume”):

json
Copy code
{"type":"client.resume","resume_token":"<string>"}
Server → Client frames

- **JSON text frames** (policy/info/chat/etc.) follow the telemetry envelope shown below. Examples:

```json
{"type":"pong","t":1730000000000}
```

- **Binary audio frames** carry raw PCM s16le chunks at **16 kHz mono**. The playback descriptor is announced once per connection via `info.audio`, which already includes `{ "codec": "pcm_s16le", "rate_hz": 16000, "channels": 1 }`. Each binary frame is a contiguous PCM buffer with no additional header bytes.

Policy (client-visible subset)
The server may push policy updates. Client-visible frames follow the telemetry
envelope (`type`, `ts_ms`, `level`, …) and the server **strips any policy keys
outside the stable subset** before sending to browsers. The client‑stable subset
is:

json
Copy code
{
  "type": "policy.interaction",
  "ts_ms": 1730000000000,
  "level": "info",  
  "policy": {
    "mode": "idle",                  // "idle" | "assistant_speaking" | ...
    "allow_auto_vad": true,
    "barge_in_enabled": true
  }
}
Other internal policy keys (e.g., telemetry, sampling) may be present in server telemetry/export, but MUST NOT be sent over WS to browsers as part of policy.interaction. (See ADR notes.)

### Persona modes & generation (informational)

- The server’s Generator composes messages using a mode-aware persona (single source of truth).
- Modes (enum): `clarify`, `outline`, `deep_dive`, `compare`, `steps`, `next_actions`.
- Each mode has a short instruction that shapes the assistant’s response; these are server-side and not sent to the client.
- The Planner selects one mode; the server may expose the selection via `dialog.plan` telemetry.

Initial info frame (emitted after handshake)
The server emits a single info frame containing connection/session hints:

json
Copy code
{
  "type": "info",
  "audio": { "codec": "pcm_s16le", "rate_hz": 16000, "channels": 1 },
  "slo": {
    "first_partial_ms": { "target": 450, "p95": 750 },
    "final_ms":         { "target": 2000, "p95": 3000 },
    "tts_start_ms":     { "target": 350,  "p95": 600 }
  },
  "voice_id": "alloy-en-US-001",
  "locale": "en-US",
  "meta": {
    "sid": "a1b2c3",
    "version": "2",
    "features": ["tts","asr","barge_in"],
    "resume_token": "rTok_4nA7...", "resume_ttl_ms": 10000
  }
}
info.audio describes server→client playback format (binary WS messages, see “Binary audio framing”).

slo provides advisory UI thresholds. The block is optional; clients MUST handle
connections where it is absent.

voice_id/locale let the client label playback.

- `voice_id` is a stable string slug that identifies the TTS configuration. It
  follows the `<provider>-<locale>-<variant>` pattern (for example
  `alloy-en-US-001`).
- `locale` is a BCP 47 language tag constrained to `ll-CC` casing (`en-US`,
  `fr-FR`, `es-ES`, …). This is the locale the server will narrate in and
  should inform captioning or playback labels.
- `persona_mode_hints`: informational array (for example,
  `["clarify","outline","deep_dive","compare","steps","next_actions"]`) that the
  server MAY include to describe available planner modes. Servers may omit this
  field without changing client behavior.

The server **always** populates `info.voice_id` and `info.locale`. Every
`tts.start` telemetry envelope mirrors the same identifiers in
`meta.voice_id`/`meta.locale` so the client can update UI state when playback
begins.

### Dialog planning (optional telemetry)

The server may emit a lightweight telemetry frame to describe planner intent:

```json
{
  "type": "dialog.plan",
  "ts_ms": 1730000000000,
  "mode": "clarify | outline | deep_dive | compare | steps | next_actions",
  "missing_info": ["intent", "details"],
  "chips": ["Share more context", "Show an example"],
  "reason": "short note for observability only"
}
```

Notes:

- Emitted at most once per user turn, after ASR final and before assistant chunks.
- Purely telemetry; clients don’t need it to function.
- The mode constrains the response style used by the NLG Generator.
- If absent, client behavior is unchanged.

### Binary audio framing (server → client TTS)

- Codec: **PCM s16le**, mono, **16 kHz** (`info.audio` announces the descriptor).
- The server emits one PCM buffer per **binary WebSocket message**. No frame headers or chunk counters are prepended.
- Each chunk length MUST be a multiple of `channels * 2` bytes (16‑bit samples).
- The existing binary routing guard still rejects non-audio payloads.
- Playback ordering is deterministic: every utterance is bracketed by a `tts.start` JSON frame, contiguous PCM binary messages, and a matching `tts.end` frame.

Client decoding example (WebAudio):

```js
const sampleRate = 16000;
const audioCtx = new AudioContext({ sampleRate });

function playPcmChunk(arrayBuffer) {
  const int16 = new Int16Array(arrayBuffer);
  const float32 = new Float32Array(int16.length);
  for (let i = 0; i < int16.length; i += 1) {
    float32[i] = Math.max(-1, Math.min(1, int16[i] / 32768));
  }

  const buffer = audioCtx.createBuffer(1, float32.length, sampleRate);
  buffer.copyToChannel(float32, 0);

  const source = audioCtx.createBufferSource();
  source.buffer = buffer;
  source.connect(audioCtx.destination);
  source.start();
}
```

Core event schemas
TTS / ASR / Error examples
json
Copy code
{"type":"tts.start","utt_id":"u-123","voice_id":"alloy-en-US-001","locale":"en-US","post_hold_ms":200}
{"type":"tts.end","utt_id":"u-123"}

{"type":"asr.ready","vendor":"deepgram","input":{"container":"webm","codec":"opus","rate_hz":48000,"channels":1}}
{"type":"asr.partial","req_id":"r-1","text":"...","confidence":0.73}
{"type":"asr.final","req_id":"r-1","text":"...","confidence":0.91}

`asr.partial` frames are **opportunistic hints**. Under backpressure the server coalesces partials,
emitting at most one every ~50 ms and keeping the latest text. The server maintains a monotonic
`partial_seq` counter; clients MUST treat gaps as normal and continue waiting for the
authoritative `asr.final` frame.

{"type":"error","code":"bad_subprotocol","message":"use chat.v2","retryable":false}
{"type":"error","code":"schema_invalid","message":"audio.header requires integer channels","retryable":false}
{"type":"error","code":"invalid_message","message":"unknown type admin.nuke","retryable":false}
Error taxonomy (normative shape):

json
Copy code
{
  "type":"error",
  "code":"rate_limited",          // enum
  "message":"Too many connections",
  "retryable": true,
  "retry_in_ms": 3000             // optional when retryable==true
}

### Error taxonomy

| Code               | Retryable | When it is emitted                                             | Retry guidance                                          |
| ------------------ | --------- | --------------------------------------------------------------- | ------------------------------------------------------- |
| `auth_failed`      | No        | Missing or invalid `access_token` query token.                 | Fix authentication and reconnect immediately.           |
| `origin_blocked`   | No        | Origin header not in allow-list.                               | Update the allow-list before attempting again.          |
| `version_mismatch` | No        | WebSocket subprotocol negotiation failed.                      | Connect with `chat.v2` and retry immediately.           |
| `rate_limited`     | Yes       | Connection/session exceeds token bucket limits.                | Wait `retry_in_ms` when provided; otherwise back off.   |
| `provider_down`    | Yes       | Downstream ASR/TTS/LLM vendor outage.                          | Retry with exponential backoff until the service recovers. |
| `resume_invalid`   | No        | Provided resume token is expired or already used.              | Start a new session and obtain a fresh resume token.    |
| `invalid_message`  | No        | JSON frame is malformed or unsupported for the current policy. | Correct the payload before retrying.                    |

`retry_in_ms` is only present when the server can provide a concrete wait time for retryable errors.

Behavioral rules
Connection & Version Negotiation
Only subprotocol chat.v2 is accepted. Clients missing it receive HTTP 426 with { "code": "bad_subprotocol" }.

Authorization, Origin, Rate Limits
Authorization: query token ?access_token=<JWT> is required unless explicitly disabled. Invalid/missing → error{code:"auth_failed"} then close 1008.

Origin allow‑list: reject disallowed origins with 403 or close 1008 + error{code:"origin_blocked"}.

Rate limits: over‑rate → error{code:"rate_limited", retry_in_ms:<ms>} then close 1008.

Frame validation
Invalid JSON → schema_invalid or invalid_message. Connection typically remains open unless otherwise specified.

Backpressure & partial coalescing
Outbound WS outbox is bounded; on overflow the server emits telemetry EVT_WS_OUTBOX_DROP and may coalesce asr.partial frames (client should expect fewer partials; asr.final is authoritative).

asr.partial MAY include partial_seq (monotonic). Under backlog, intermediate partials MAY be dropped; the most recent text is delivered at most once per throttle interval.

Mode & Barge rules
While mode: "assistant_speaking":

If barge_in_enabled: true → auto‑VAD or ASR evidence may interrupt TTS.

If false → ignore speech until tts.end + post_hold_ms.
In mode: "idle", allow_auto_vad MUST be true and ACWR effective.

Telemetry Envelope v1
All events share this shape:

json
Copy code
{
  "schema_version":"1",
  "type":"EVT_*",
  "ts_ms":1730000000000,
  "sid":"session-uuid",
  "turn_id":"turn-uuid",
  "req_id":"req-uuid",
  "who":"client|server|asr|tts|llm",
  "source":"webapp|ws_server|deepgram|speechmatics|elevenlabs|openai|policy",
  "level":"debug",
  "meta":{...}
}
Server normalization fills timestamps, levels, and redacts PII/secrets.

Extended event catalog (Builds 04–07)
Engine & Gate events
json
Copy code
{"type":"EVT_ENGINE_STATE","from":"Idle","to":"AssistantSpeaking","ts_ms":...}
{"type":"EVT_MIC_GATE","effective":true,"reasons":["tts_active"],"ts_ms":...}
{"type":"EVT_TTS_MASK","phase":"on","ts_ms":...}
{"type":"EVT_BARGE_IN","source":"auto_vad","granted":false,"reason":"policy_disabled","ts_ms":...}
reasons: tts_active | manual_gate | system_hold | error_hold

states: Idle | AssistantSpeaking | ConfirmingBarge | Listening | UserTurnStreaming | Thinking

ASR events (multivendor)
json
Copy code
{"type":"EVT_ASR_READY","vendor":"deepgram"}
{"type":"EVT_ASR_PARTIAL","req_id":"r-1","text":"...","confidence":0.8}
{"type":"EVT_ASR_FINAL","req_id":"r-1","text":"...","confidence":0.92}
vendor: deepgram | speechmatics

One FINAL per turn.

Policy / NLU / NLG
json
Copy code
{"type":"EVT_POLICY_DECISION","req_id":"r-1","action":"respond","barge_in_enabled":true,"auto_commit_when_ready":true,"ts_ms":...}
{"type":"EVT_NLU","req_id":"r-1","intent":"troubleshoot_install","entities":{"product":"FlashArray"},"confidence":0.86,"ts_ms":...}
{"type":"EVT_NLG","req_id":"r-1","text":"Let's run through a quick install check…","ts_ms":...}
Exactly one NLU and one NLG per turn (req_id stable).

Telemetry / Vendor debug
json
Copy code
{"type":"EVT_TELEMETRY_POLICY","enabled":true,"level":"info","categories":["engine","asr"],"sampling":{"rate":1.0},"ts_ms":...}
{"type":"EVT_VENDOR_DEBUG","channel":"asr","vendor":"deepgram","rid":"opaque","timings":{"first_partial_ms":141},"ts_ms":...}
All fields subject to redaction rules.

Client playback telemetry (optional)
json
Copy code
{"type":"EVT_PLAYBACK","phase":"start","media_id":"opt","ts_ms":...}
phase: start | end — client informational only.

TTS audio framing (server → client)
The server sends binary WS messages for TTS audio.

Framing: one binary message == one contiguous PCM s16le chunk (rate_hz: 16000, channels: 1).

The info.meta.tts_audio descriptor declares { "codec":"pcm_s16le","rate_hz":16000,"channels":1 } so the client can initialize WebAudio.

Exporter Packaging (Build 06)
Each session export under exports/<sid>/ includes (packaged deterministically; redaction is re‑applied before write):

manifest.json (counts, first/last timestamps, SHA‑256 map, truncation info)

events.redacted.ndjson (one JSON line per event; post‑redaction)

flow_timeline.ndjson (turn/flow markers)

nlu.ndjson and nlg.ndjson (one record per user turn; may be empty)

README.txt (summary and truncation notice if size‑capped)

Note: Previously listed artifacts like server.log, ws_in.ndjson, ws_out.ndjson are not part of the packaged ZIP for privacy/size reasons. (Redaction is applied during packaging; file set is fixed and ordered.)

Client Reconnect / Resume (Build 07)
The initial info frame includes a resume_token and resume_ttl_ms.

To resume within TTL:

bash
Copy code
wss://<host>/ws/v2/chat?access_token=<JWT>&resume=<resume_token>
Sec-WebSocket-Protocol: chat.v2
Origin: https://app.askchip.ai
On success, the server re‑attaches to the same sid and replays recent markers (tts.start/end, asr.final) as JSON (audio is not replayed).

Expired/invalid → error{code:"resume_invalid", retryable:false} then close 1008.

ASR readiness gate
Binary audio frames are accepted only after the server has emitted the appropriate {"type":"asr.ready","vendor":"<name>","input":{...}}.
Before ASR is ready, inbound audio is rejected with:
{"type":"error","code":"audio_not_expected","message":"ASR not ready","retryable":false} followed by a WebSocket close 1003.

Binary audio sequencing & jitter buffer (Build 5)
To handle network reordering and loss, the server maintains a per‑session sequence for inbound audio frames:

The client may (optionally) signal a starting index with audio.header via {"seq_start": <int>}.

Each subsequent binary frame is assigned a monotonically increasing seq by the adapter (when the client does not provide an explicit index).

The server keeps a reordering window W (default 8 frames). Frames within the window may be reordered; frames older than the window are dropped.

When the server detects one or more missing frames, it emits a diagnostic telemetry event:
{"type":"EVT_AUDIO_GAP","from_seq": <int>,"to_seq": <int>}

Oversized frames trigger {"type":"error","code":"frame_too_large"} then WS close 1009.

Malformed or contradictory headers (e.g., PCM 16k declared but 48k sent) trigger {"type":"error","code":"schema_invalid"} then WS close 1003.

Chat frames (Build 5 server support)
Client → Server (typed user input)
The client may send a typed message at any time (including while the assistant is speaking):

json
Copy code
{"type":"chat.user","text":"<utf8 text>","client_msg_id":"<optional client UUID>"}
Max size: 64 KiB (same as JSON text frame limit).

Bad UTF‑8 ⇒ close 1007. Other schema issues ⇒ {"type":"error","code":"schema_invalid"} then close 1003.

If barge_in_enabled:true and the assistant is speaking, chat.user is treated as barge‑in with source:"text".

Server → Client (messages & history)
Single message:

json
Copy code
{"type":"chat.message","id":"<uuid>","role":"user|assistant","text":"<utf8>","origin":"voice|text","turn_id":"<uuid>","req_id":"<uuid>","ts_ms":1730000000000}
History snapshot (sent on connect/resume):

json
Copy code
{"type":"chat.history","messages":[ /* array of chat.message */ ], "next_cursor": null}
Dual‑VAD policy & diagnostics (Build 5)
The server fuses auto‑VAD (energy/activity) and ASR evidence into a single barge decision during policy.mode:"assistant_speaking". Clients do not need to change behavior; this section documents server‑side policy and diagnostic telemetry.

Policy (server‑side) — policy.interaction.vad block:

json
Copy code
"vad": {
  "mode": "or",                 // "or" | "and" | "priority"
  "priority": "asr",            // "asr" | "auto" (used when mode=="priority")
  "min_speech_ms": 200,         // speech must persist >= this to count
  "energy_threshold_dbfs": -45, // baseline; may adapt per session via SNR
  "hold_ms": 200,               // hysteresis to avoid flapping
  "echo_suppression_ms": 350,   // ignore mic after tts.start
  "barge_cooldown_ms": 250      // avoid re‑barge storms
}
Diagnostic telemetry (server‑emitted; not WS contract frames):

json
Copy code
{"type":"EVT_VAD","source":"auto_vad","phase":"start","metric":{"dbfs":-28.0,"active_ms":220},"ts_ms":1730000000000}
{"type":"EVT_VAD","source":"asr_evidence","phase":"start","metric":{"confidence":0.82},"ts_ms":1730000000500}
{"type":"EVT_VAD_DECISION","mode":"or","granted":true,"reasons":["asr_conf>=0.75","auto_active>=200ms"],"ts_ms":1730000000550}
Text barge‑in: If a {"type":"chat.user"} arrives while policy.mode:"assistant_speaking" and barge_in_enabled:true, the server treats it as barge‑in with source:"text" and follows the standard mask/gate/tts‑cancel path.

Document last updated: 2025-10-22T17:10:00Z

markdown
Copy code
