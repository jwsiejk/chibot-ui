# WebSocket Contract — `chat.v2`

**Endpoint:** `/ws/v2/chat` (ASGI WS)  
**Required subprotocol:** `chat.v2`

---

## Client → Server frames
- `{"type":"ping"}`
- `{"type":"client.ready"}`
- **Audio:** binary frames (Opus or PCM).  
  If format changes, send a header JSON first:
  ```json
  {"type":"audio.header","format":"opus","sample_rate":48000,"channels":1}
Optional admin toggle (if exposed):

json
Copy code
{"type":"admin.toggle","asr":"deepgram","barge_in_enabled":true}
Optional reconnect/resume handshake (Build 07):

json
Copy code
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
      "level": "debug",               // trace|debug|info|warn|error
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
TTS / ASR / Error examples
json
Copy code
{"type":"tts.start","utt_id":"u-123","post_hold_ms":200}
{"type":"tts.end","utt_id":"u-123"}
{"type":"asr.ready":true}
{"type":"asr.partial","text":"...","confidence":0.73}
{"type":"asr.final","text":"...","confidence":0.91}
{"type":"error","code":"bad_subprotocol","detail":"use chat.v2"}
{"type":"error","code":"schema_invalid","detail":"audio.header requires integer channels"}

Behavioral rules
Connection & Version Negotiation
The server only supports subprotocol "chat.v2".
Clients proposing a different or missing subprotocol receive HTTP 426 with
{"type":"error","code":"bad_subprotocol","detail":"use chat.v2"} before upgrade.

Once upgraded, all subsequent error frames follow the same shape {type:"error","code","detail"}.

Authorization & Rate Limits
A valid Authorization: Bearer <token> header is required unless disabled in configuration.

Missing or invalid auth →
{"type":"error","code":"unauthorized","detail":"missing or invalid auth"}.

Excessive frame or byte rate →
{"type":"error","code":"rate_limited","detail":"try later"} followed by WS close (1008).

Frame Validation
Text frames must contain valid JSON and minimal schema requirements.
Invalid frames return:

json
Copy code
{"type":"error","code":"schema_invalid","detail":"<validation hint>"}
The connection remains open unless otherwise specified.

Backpressure
When outbound queue depth exceeds threshold, the server publishes
EVT_BACKPRESSURE_ON and EVT_BACKPRESSURE_OFF (diagnostic only).

Mode & Barge rules
While mode="assistant_speaking":

If barge_in_enabled=true, auto-VAD + ASR evidence may interrupt TTS.

If barge_in_enabled=false, ignore speech until tts.end + post_hold_ms.

In mode="idle", allow_auto_vad MUST be true and ACWR effective (subject to admin kill).

Telemetry event envelope v1 (both sides)
All events use schema v1 and share this shape:

json
Copy code
{
  "schema_version": "1",
  "type": "EVT_*",
  "ts_ms": 1730000000000,
  "sid": "session-uuid",
  "turn_id": "turn-idx-or-uuid",
  "who": "client|server|asr|tts|llm",
  "source": "webapp|ws_server|deepgram|speechmatics|elevenlabs|openai|policy",
  "level": "debug",
  "meta": { ... }
}
Server normalization

Fills missing ts_ms and level.

Applies best-effort redaction to meta string fields
(emails, authorization/bearer tokens, query secrets, opaque tokens, oversized blobs).

Optional fields may be added without a schema bump; changing required fields requires a new schema_version.

Common meta fields by category
Category	Example fields
policy.diff	{ "allow_auto_vad":[old,new], "barge_in_enabled":[old,new], "auto_commit_when_ready":[old,new], "mode":[old,new], "telemetry.level":[old,new] }
gate	`{ "state":"on
barge	`{ "source":"auto_vad
tts	{ "utt_id":"...", "post_hold_ms":200 }
asr	`{ "req_id":"...", "partial":true
ws taps	`{ "dir":"in
backpressure	`{ "queue_depth":123, "state":"on
nlu/nlg	full NLU or NLG object (see docs/15_NLU_NLG.md)
error	{ "code":"...", "detail":"...", "stack":"(optional)" }

Exporter Packaging (Build 06)
Each session export under exports/<sid>/ includes:

events.ndjson — one JSON line per event (Envelope v1).

manifest.json — counts by type, first/last timestamps, SHA-256 checksums.

README.txt — summary and truncation notice (if size capped).

ZIPs include all files; redaction is re-applied before write.

Client Reconnect/Resume (Build 07)
Clients may attempt to resume a prior session via:

json
Copy code
{"type":"client.resume","resume_token":"<string>"}
Servers may echo the same token in policy.interaction or info frames for continuation.

(End of Document)