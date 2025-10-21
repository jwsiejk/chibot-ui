# BUILD 01 — Server v2 Bootstrap

**Alignment guard (do not omit):**
- Align with SSOT in `/docs` (`00_CONTEXT.md`, `10_CONTRACT_WS.md`, `20_ARCH_BUILD_ORDER.md`, and `30_ADR.md` where applicable).
- Touch only the files listed per task. Do **not** rename routes, env vars, or policy keys.
- Keep each new/changed file ≤ **500** lines; ≤ **3** files per task.
- Preserve the `chat.v2` WebSocket contract (no policy frames in Build 01).
- All telemetry envelopes must follow the unified schema introduced here.

---

### B1-A: Telemetry Bus + Event Constants
**Files:** `app/telemetry/bus.py` (new), `app/voice_v2/__init__.py` (new or update)  
**Non-goals:** Exporters, WS code, policy/ASR/TTS, DB/ENV, external deps  
**Acceptance:**  
- `subscribe(event_type)` returns unique token; `unsubscribe(token)` works.  
- `publish(event)` normalizes envelope (`type`, default `ts_ms`, default `level="debug"`), dispatches to exact + `"*"` subscribers, isolates handler exceptions.  
- Envelope fields supported per your spec (sid, turn_id, who, source, level, meta).  
- Event constants defined for WS, TTS, Policy, Gate, ASR/NLU/NLG and exported via `__all__`.  
- No side effects on import; typed, documented; ≤ 300 LOC per file.

---

### B1-B: WebSocket Adapter v2 (chat.v2) Handshake & Frame Router
**Files:** `app/ws/adapter.py` (new), `app/asgi_gateway.py` (new or update)  
**Non-goals:** ASR/TTS/NLU/NLG/policy logic; exporter persistence; DB/ENV; binary decoding; UI beyond health  
**Acceptance:**  
- Route matches SSOT; requires subprotocol `chat.v2`; otherwise HTTP 426 JSON: `{"error":"unsupported_subprotocol","expected":"chat.v2"}`.  
- On upgrade, generate `sid`; publish `EVT_WS_OPEN`/`EVT_WS_CLOSE`; tap `EVT_WS_JSON_RECV/SEND`, `EVT_WS_AUDIO_RECV/SEND` with byte_count/seq.  
- JSON validation: bad JSON → `{"type":"error","code":"bad_json"}` (no drop). Unknown type → `{"type":"error","code":"unknown_type"}`.  
- Frame size guards (text 64KB, binary 2MB); oversize → error + close 1009.  
- Ping/Pong supported; clean close; `/api/v1/health` returns `{"ok":true,"engine":"v2","ws_subprotocol":"chat.v2"}`.

---

### B1-C: Engine v2 Shell + Minimal File Exporter (session capture)
**Files:** `app/voice_v2/engine.py` (new), `app/telemetry/exporter.py` (new)  
**Non-goals:** Policy frames/diffs; ASR/LLM/TTS; DB/ENV; compression/cloud upload; UI  
**Acceptance:**  
- `EngineV2` exposes: `on_open/on_json/on_audio/on_close`, each publishes telemetry (WS_* events) then writes normalized event to exporter.  
- `FileExporter.begin/write/end` writes `exports/<sid>/events.ndjson` (NDJSON, append-only) and `manifest.json` with counts, first/last ts.  
- Adapter wires `begin` on open and `end` on close with summary `{close_code}`.  
- Exporter safe on restarts (open-per-write or flush). Typed, documented; ≤ 500 LOC/file.

---

### B1-D: Telemetry Envelope v1 + Redaction Rules (Governance)
**Files:** `docs/30_ADR.md` (new ADR entry), `docs/10_CONTRACT_WS.md` (update), `app/telemetry/bus.py` (update)  
**Non-goals:** Full PII catalog, DB retention, zip packaging (later builds)  
**Acceptance:**  
- Document `schema_version: "1"`; default redaction for emails/tokens in telemetry `meta` using simple patterns; allow opt-out via future policy.telemetry.  
- Bus applies redaction on `publish()` only to `meta` subfields (non-destructive) before dispatch.  
- ADR explains rationale, compat policy, and time source (epoch ms).  
- Unit smoke: demonstrates redaction of `user@example.com` and `Bearer abc123`.

---

### B1-E: Health / Live / Ready / Info
**Files:** `app/asgi_gateway.py` (update)  
**Non-goals:** Auth, DB probes, provider checks  
**Acceptance:**  
- `/api/v1/health` (basic ok), `/api/v1/live` (process up), `/api/v1/ready` (export path writable), `/api/v1/info` (git SHA, build time, version string).  
- Readiness fails if export root not writable; JSON responses; typed helpers.

> “Return only diffs for the files listed above. Do not modify or create any other files.”
