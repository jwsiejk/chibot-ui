# WebSocket Contract — `chat.v2`

**Endpoint:** `/ws/v2/chat` (ASGI WS)  
**Required subprotocol:** `chat.v2`  
**Deprecated:** `/ws/v1/chat` → HTTP 410 Gone (JSON `{"error":"gone"}`)

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
/ws/v1/chat returns 410 Gone (JSON body).

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
