# WebSocket Contract — `chat.v2`

**Endpoint:** `/ws/v2/chat` (ASGI WS)  
**Required subprotocol:** `chat.v2`

## Client → Server frames
- `{"type":"ping"}`
- `{"type":"client.ready"}`
- **Audio:** binary frames (Opus or PCM). If format changes, send a header JSON first:
  ```json
  {"type":"audio.header","format":"opus","sample_rate":48000,"channels":1}
Optional admin toggle (if exposed):

json
Copy code
{"type":"admin.toggle","asr":"deepgram","barge_in_enabled":true}
Server → Client frames
{"type":"pong","t":1730000000000}

Policy (always complete):

json
Copy code
{
  "type":"policy.interaction",
  "policy":{
    "mode":"assistant_speaking"|"idle",
    "allow_auto_vad":true,
    "barge_in_enabled":true,
    "auto_commit_when_ready":true,
    "telemetry":{
      "enabled":true,
      "level":"debug",               // trace|debug|info|warn|error
      "categories":{
        "ws":true,"audio":true,"policy":true,"tts":true,"gate":true,
        "barge":true,"asr":true,"nlu":true,"nlg":true,"client_ui":true,"provider_debug":true
      },
      "redaction":{"pii":true,"secrets":true,"text":false},
      "sampling":{"percent":100}
    }
  }
}
{"type":"tts.start","utt_id":"u-123","post_hold_ms":200}

{"type":"tts.end","utt_id":"u-123"}

{"type":"asr.ready":true}

{"type":"asr.partial","text":"...","confidence":0.73}

{"type":"asr.final","text":"...","confidence":0.91}

{"type":"error","code":"bad_subprotocol","detail":"use chat.v2"}

Behavioral rules
While mode="assistant_speaking":

If barge_in_enabled=true, auto-VAD + ASR evidence may interrupt TTS.

If barge_in_enabled=false, ignore speech until tts.end + post_hold_ms.

In mode="idle", allow_auto_vad MUST be true and ACWR effective (subject to admin kill).

Telemetry event envelope (both sides)
All events (client/server) share this shape:

json
Copy code
{
  "type":"EVT_*",
  "ts_ms":1730000000000,
  "sid":"session-uuid",
  "turn_id":"turn-idx-or-uuid",
  "who":"client|server|asr|tts|llm",
  "source":"webapp|ws_server|deepgram|speechmatics|elevenlabs|openai|policy",
  "level":"debug",
  "meta":{ ... }
}
Common meta fields by category
policy.diff → { "allow_auto_vad":[old,new], "barge_in_enabled":[old,new], "auto_commit_when_ready":[old,new], "mode":[old,new], "telemetry.level":[old,new] }

gate → { "state":"on|off", "reason":"tts|post_hold|policy", "mask":true|false }

barge → { "source":"auto_vad|asr_evidence", "granted":true|false }

tts → { "utt_id":"...", "post_hold_ms":200 }

asr → { "req_id":"...", "partial":true|false, "confidence":0.83 }

nlu → full NLU object (see docs/15_NLU_NLG.md)

nlg → full NLG object (see docs/15_NLU_NLG.md)

ws → { "dir":"in|out", "size":1234, "preview":"{...}" }

error → { "code":"...", "detail":"...", "stack":"(optional)" }
