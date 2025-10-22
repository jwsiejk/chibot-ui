# WebSocket Contract — `chat.v2`

**Endpoint:** `/ws/v2/chat` (ASGI WS)  
**Required subprotocol:** `chat.v2`  


---

## Client → Server frames
- `{"type":"ping"}`
- `{"type":"client.ready"}`
- **Audio:** binary frames (Opus or PCM).  
  Default: WebM/Opus, mono, 48 kHz.  
  To override, send:
  ```json
  {"type":"audio.header","format":"opus","sample_rate":48000,"channels":1}
Optional admin toggle:
{"type":"admin.toggle","asr":"deepgram","barge_in_enabled":true}

Optional resume handshake (Build 07):
{"type":"client.resume","resume_token":"<string>"}

Server → Client frames
{"type":"pong","t":1730000000000}

Policy (always complete)
json
Copy code
{
  "type": "policy.interaction",
  "policy": {
    "mode": "idle",
    "allow_auto_vad": true,
    "barge_in_enabled": true,
    "auto_commit_when_ready": true,
    "telemetry": {
      "enabled": true,
      "level": "debug",
      "categories": {
        "ws": true,
        "audio": true,
        "policy": true,
        "tts": true,
        "gate": true,
        "barge": true,
        "asr": true,
        "nlu": true,
        "nlg": true,
        "client_ui": true,
        "provider_debug": true
      },
      "redaction": { "pii": true, "secrets": true, "text": false },
      "sampling": { "percent": 100 }
    }
  }
}
Core event schemas
TTS / ASR / Error examples
json
Copy code
{"type":"tts.start","utt_id":"u-123","post_hold_ms":200}
{"type":"tts.end","utt_id":"u-123"}
{"type":"asr.ready","vendor":"deepgram"}
{"type":"asr.partial","req_id":"r-1","text":"...","confidence":0.73}
{"type":"asr.final","req_id":"r-1","text":"...","confidence":0.91}
{"type":"error","code":"bad_subprotocol","detail":"use chat.v2"}
{"type":"error","code":"schema_invalid","detail":"audio.header requires integer channels"}
{"type":"error","code":"unknown_type","detail":"admin.nuke"}
Behavioral rules
Connection & Version Negotiation
Only subprotocol chat.v2 is accepted.
Clients missing it receive HTTP 426 with {"code":"bad_subprotocol"}.


Authorization & Rate Limits
Bearer token required unless disabled.
Invalid → {"code":"unauthorized"}.
Over-rate → {"code":"rate_limited"} then close 1008.

Frame validation
Invalid JSON → schema_invalid or unknown_type.
Connection remains open unless otherwise specified.

Backpressure
Server publishes EVT_BACKPRESSURE_ON / EVT_BACKPRESSURE_OFF (diagnostic).

Mode & Barge rules
While mode:"assistant_speaking":

If barge_in_enabled:true → auto-VAD or ASR evidence may interrupt TTS.

If false → ignore speech until tts.end + post_hold_ms.
In mode:"idle", allow_auto_vad MUST be true and ACWR effective.

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

Extended event catalog (Builds 04-07)
Engine & Gate events
json
Copy code
{"type":"EVT_ENGINE_STATE","from":"Idle","to":"AssistantSpeaking","ts_ms":...}
{"type":"EVT_MIC_GATE","effective":true,"reasons":["tts_active"],"ts_ms":...}
{"type":"EVT_TTS_MASK","phase":"engaged","ts_ms":...}
{"type":"EVT_BARGE_IN","source":"auto_vad","granted":false,"reason":"policy_disabled","ts_ms":...}
reasons: tts_active | manual_gate | system_hold | error_hold

States: Idle | AssistantSpeaking | ConfirmingBarge | Listening | UserTurnStreaming | Thinking

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
All fields subject to redaction rules (ADR-0009).

Client playback telemetry (optional)
json
Copy code
{"type":"EVT_PLAYBACK","phase":"start","media_id":"opt","ts_ms":...}
phase: start | end — client informational only.

Exporter Packaging (Build 06)
Each session export under exports/<sid>/ includes:

events.ndjson (one JSON line per event)

manifest.json (counts, first/last timestamps, SHA-256 hashes)

server.log, ws_in.ndjson, ws_out.ndjson, flow_timeline.ndjson,
nlu.ndjson and nlg.ndjson (one record per user turn)

README.txt (summary and truncation notice if size-capped)

ZIPs include all files; redaction is re-applied before write.

Client Reconnect / Resume (Build 07)
Clients may attempt to resume a prior session:

json
Copy code
{"type":"client.resume","resume_token":"<string>"}
Servers may echo the same token in policy.interaction or info frames for continuation.

(End of Document)

yaml
Copy code

---

### ASR readiness gate

Binary audio frames are **accepted only after** the server has emitted `{"type":"asr.ready","vendor":"<name>"}`.  
Before ASR is ready, inbound audio is rejected with:  
`{"type":"error","code":"audio_not_expected"}` followed by a WebSocket close **1003**.

### Binary audio sequencing & jitter buffer (Build 5)

To handle network reordering and loss, the server maintains a **per-session** sequence for inbound audio frames:

- The client may (optionally) signal a starting index with `audio.header` via `{"seq_start": <int>}`.
- Each subsequent **binary** frame is assigned a monotonically increasing **`seq`** by the adapter (when the client does not provide an explicit index).
- The server keeps a reordering window **W** (default **8 frames**). Frames within the window may be reordered; frames **older than the window** are **dropped**.
- When the server detects one or more missing frames, it emits a diagnostic telemetry event:  
  `{"type":"EVT_AUDIO_GAP","from_seq": <int>,"to_seq": <int>}`
- Oversized frames trigger `{"type":"error","code":"frame_too_large"}` then WS close **1009**.
- Malformed or contradictory headers (e.g., PCM 16k declared but 48k sent) trigger `{"type":"error","code":"schema_invalid"}` then WS close **1003**.

**`audio.header` example (extended):**
```json
{"type":"audio.header","format":"opus","sample_rate":48000,"channels":1,"seq_start":0}
```

---

## Build 6 updates (Exporter, Admin Flow, Backpressure clarifications)

### Server → Client frames (allow‑list unchanged)
Build 6 **does not expand** the set of server→client messages. Only these JSON frames are ever sent from server to client over the `chat.v2` socket:

- `"policy.interaction"`
- `"info"`
- `"tts.start"`, `"tts.end"`
- `"asr.ready"`, `"asr.partial"`, `"asr.final"`
- `"error"`

All other events are **server‑side telemetry only** (available via the Admin Flow endpoints below) and **will never be sent over WS**.

### Outbound WS bridge & backpressure semantics
The adapter maintains a bounded outbox queue (default **256**). On overflow the newest message is dropped and a telemetry event
`EVT_WS_OUTBOX_DROP` is recorded (not emitted to the client). Implementations **may coalesce** `asr.partial` messages under sustained load;
clients **must not** assume every partial is delivered.

### Telemetry privacy & redaction
Telemetry is normalized and redacted **before** it is written to disk or streamed via admin APIs. Sensitive fields (auth tokens, emails,
opaque provider IDs) are masked. WS frames never include these fields.

### Admin Flow (HTTP, not WS)
Build 6 introduces read‑only Admin Flow endpoints for observability. These are **not** part of the WS contract, but are included here for clarity:

- `GET /api/v1/admin/flow/{sid}/trace?type=EVT_X,EVT_Y&since_ms=<ts>&limit=<n>`  
  Responds with **application/x-ndjson**; lines are redacted telemetry for the session. Requires `Authorization: Bearer ...`.
- `GET /api/v1/admin/flow/{sid}/zip`  
  Responds with **application/zip** containing a deterministic package of redacted artifacts.

### Export artifacts (for reference)
The packaged zip contains exactly:
- `manifest.json` (stable key order; includes `sha256` of packaged files and a `summary` block)
- `events.redacted.ndjson` (full redacted stream)
- `flow_timeline.ndjson` (turn/flow markers only)
- `nlu.ndjson`, `nlg.ndjson` (may be empty)
- `README.txt`

Total package size is capped (default **25 MB**). If truncation occurs, `manifest.json` includes a `truncated` section describing what was dropped.

### Performance telemetry (server‑side)
For each turn the engine records:
- `t_first_partial_ms` — time from turn begin to first ASR partial
- `t_final_ms` — time to ASR final
- `t_tts_start_ms` — time to first TTS audio

These appear in telemetry as `EVT_PERF_SUMMARY` and in `manifest.json.summary` in the exported zip. They are **not** sent over WS.

*Document last updated:* 2025-10-22T07:21:07Z
