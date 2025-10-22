# WebSocket Contract — `chat.v2`

**Endpoint:** `/ws/v2/chat` (ASGI WS)  
**Required subprotocol:** `chat.v2`

---

## Connection semantics (strict v2-only)

- **Unknown path** → **HTTP 404** (no upgrade).
- **Missing/invalid subprotocol** → **HTTP 426** (Upgrade Required). Body may include a short JSON error: `{"type":"error","code":"bad_subprotocol"}`.
- **Auth failure** (e.g., missing/invalid bearer) → **HTTP 401** with JSON `{"type":"error","code":"unauthorized"}` (pre-upgrade).
- This is a **clean v2 rebuild**. There are **no legacy v1 routes or shims**.

**Auth header (required):**
```
Authorization: Bearer <token>
```

---

## Client → Server frames

- `{"type":"ping"}`
- `{"type":"client.ready"}`
- **Audio**: binary frames (Opus or PCM).
  - Default: **WebM/Opus**, mono, 48 kHz.
  - To override, send:
    ```json
    {"type":"audio.header","format":"opus","sample_rate":48000,"channels":1}
    ```
- **Optional admin toggle**:
  ```json
  {"type":"admin.toggle","asr":"deepgram","barge_in_enabled":true}
  ```
- **Optional resume handshake (Build 07)**:
  ```json
  {"type":"client.resume","resume_token":"<string>"}
  ```

---

## Server → Client frames

- `{"type":"pong","t":1730000000000}`

**Policy (always complete snapshot)**
```json
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
```

**Core event examples (TTS / ASR / Error)**
```json
{"type":"tts.start","utt_id":"u-123","post_hold_ms":200}
{"type":"tts.end","utt_id":"u-123"}

{"type":"asr.ready","vendor":"deepgram"}
{"type":"asr.partial","req_id":"r-1","text":"...","confidence":0.73}
{"type":"asr.final","req_id":"r-1","text":"...","confidence":0.91}

{"type":"error","code":"bad_subprotocol","detail":"use chat.v2"}
{"type":"error","code":"schema_invalid","detail":"audio.header requires integer channels"}
{"type":"error","code":"unknown_type","detail":"admin.nuke"}
```

---

## Behavioral rules

### Version & subprotocol
Only subprotocol **chat.v2** is accepted. Clients missing it receive **HTTP 426** with `{"code":"bad_subprotocol"}`.

### Authorization & rate limits
- Bearer token required (unless explicitly disabled in a test build).
- Invalid → `{"type":"error","code":"unauthorized"}` (HTTP 401, pre-upgrade).
- Over-rate → `{"type":"error","code":"rate_limited"}` **then** WS close **1013** (“try again later”).

### Frame validation
- Bad UTF-8 text → close **1007**.
- Bad JSON → send `{"type":"error","code":"bad_json"}` then close **1003**.
- Schema invalid (known type, wrong shape) → `{"type":"error","code":"schema_invalid"}` then close **1003**.
- Unknown `type` → `{"type":"error","code":"unknown_type"}` then close **1003**.
- Text frame too large (> 64 KiB) → `{"type":"error","code":"frame_too_large"}` then close **1009**.
- Binary audio not expected in current state → `{"type":"error","code":"audio_not_expected"}` then close **1003**.

### Backpressure (diagnostic)
- Server publishes `EVT_BACKPRESSURE_ON` / `EVT_BACKPRESSURE_OFF` (includes `queue_depth` and hysteresis; typical thresholds: on ≥12, off ≤6).

### Mode & barge rules (engine truth)
While `policy.mode:"assistant_speaking"`:
- If `barge_in_enabled:true` → auto-VAD or ASR evidence may interrupt TTS, subject to engine state and policy.
- If `barge_in_enabled:false` → ignore user speech until `tts.end` + `post_hold_ms`.
In `mode:"idle"`, `allow_auto_vad` **must be true** and ACWR must be effective.

---

## Telemetry Envelope v1 (canonical)

All events share this envelope; server normalization fills timestamps, levels, and redacts PII/secrets.

```json
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
  "meta":{ }
}
```

---

## Extended event catalog (Builds 04–07)

### Engine & Gate
```json
{"type":"EVT_ENGINE_STATE","from":"Idle","to":"AssistantSpeaking","ts_ms":...}
{"type":"EVT_MIC_GATE","effective":true,"reasons":["tts_active"],"ts_ms":...}
{"type":"EVT_TTS_MASK","phase":"engaged","ts_ms":...}
{"type":"EVT_TTS_MASK","phase":"cleared","ts_ms":...}
{"type":"EVT_BARGE_IN","source":"auto_vad","granted":false,"reason":"policy_disabled","ts_ms":...}
```
- `reasons`: `tts_active` | `manual_gate` | `system_hold` | `error_hold`  
- `states`: `Idle` | `AssistantSpeaking` | `ConfirmingBarge` | `Listening` | `UserTurnStreaming` | `Thinking`

### ASR (multivendor)
```json
{"type":"EVT_ASR_READY","vendor":"deepgram"}
{"type":"EVT_ASR_PARTIAL","req_id":"r-1","text":"...","confidence":0.8}
{"type":"EVT_ASR_FINAL","req_id":"r-1","text":"...","confidence":0.92}
```
- `vendor`: `deepgram` | `speechmatics`  
- **Exactly one FINAL per turn.**

### Policy / NLU / NLG
```json
{"type":"EVT_POLICY_DECISION","req_id":"r-1","action":"respond","barge_in_enabled":true,"auto_commit_when_ready":true,"ts_ms":...}
{"type":"EVT_NLU","req_id":"r-1","intent":"troubleshoot_install","entities":{"product":"FlashArray"},"confidence":0.86,"ts_ms":...}
{"type":"EVT_NLG","req_id":"r-1","text":"Let's run through a quick install check…","ts_ms":...}
```
- **Exactly one NLU and one NLG per turn**; `req_id` is stable across the turn.

### Telemetry / Vendor debug
```json
{"type":"EVT_TELEMETRY_POLICY","enabled":true,"level":"info","categories":["engine","asr"],"sampling":{"rate":1.0},"ts_ms":...}
{"type":"EVT_VENDOR_DEBUG","channel":"asr","vendor":"deepgram","rid":"opaque","timings":{"first_partial_ms":141},"ts_ms":...}
```
- All fields subject to redaction rules (ADR-0009).

### Client playback telemetry (optional)
```json
{"type":"EVT_PLAYBACK","phase":"start","media_id":"opt","ts_ms":...}
{"type":"EVT_PLAYBACK","phase":"end","media_id":"opt","ts_ms":...}
```
- `phase`: `start` | `end` (informational only)

---

## Exporter Packaging (Build 06)

Each session export under `exports/<sid>/` includes:

- `events.ndjson` (one JSON line per event)
- `manifest.json` (counts, first/last timestamps, SHA-256 hashes)
- `server.log`, `ws_in.ndjson`, `ws_out.ndjson`, `flow_timeline.ndjson`
- `nlu.ndjson` and `nlg.ndjson` (one record per user turn)
- `README.txt` (summary and truncation notice if size-capped)

ZIPs include all files; **redaction is re-applied** before write.

---

## Client Reconnect / Resume (Build 07)

Clients may attempt to resume a prior session:
```json
{"type":"client.resume","resume_token":"<string>"}
```
Servers may echo the same token in `policy.interaction` or `info` frames for continuation.

---

## Error taxonomy (canonical)

The server sends a concise JSON error frame **then** closes with the listed WS code (when applicable). Clients should rely on the JSON `code` for logic and treat the close code as advisory.

| Condition                                   | Error frame sent?                                | WS Close | HTTP |
|---------------------------------------------|--------------------------------------------------|:--------:|:----:|
| Missing/invalid subprotocol                  | —                                                |    —     | 426  |
| Auth missing/invalid                         | `{"type":"error","code":"unauthorized"}`         |    —     | 401  |
| Unknown WS path                              | —                                                |    —     | 404  |
| Unknown `type` in inbound JSON               | `{"type":"error","code":"unknown_type"}`         |  1003    |  —   |
| Bad UTF-8 in text frame                      | —                                                |  1007    |  —   |
| Bad JSON (parse error)                       | `{"type":"error","code":"bad_json"}`             |  1003    |  —   |
| Schema invalid (known type, bad shape)       | `{"type":"error","code":"schema_invalid"}`       |  1003    |  —   |
| Text frame too large (> 64 KiB)              | `{"type":"error","code":"frame_too_large"}`      |  1009    |  —   |
| Binary audio not expected in current state   | `{"type":"error","code":"audio_not_expected"}`   |  1003    |  —   |
| Rate limited                                 | `{"type":"error","code":"rate_limited"}`         |  1013    |  —   |
| Server going away / deploy                   | —                                                |  1012    |  —   |

(End of Document)
