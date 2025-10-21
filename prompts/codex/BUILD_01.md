# BUILD 01 — Server v2 Bootstrap

**Alignment guard (do not omit):**
- This build MUST align with the SSOT docs in `/docs` and all previous builds.
- Do not touch files outside the listed scope.
- Do not rename routes, env vars, or policy keys.
- Every new/changed file ≤ 500 lines. Keep changes small and isolated.
- Preserve the `chat.v2` contract and include the telemetry block in policy frames.

## Tasks
### B1-A: Telemetry Bus
**Files:** app/telemetry/bus.py, app/voice_v2/__init__.py
**Non-goals:** No exporter, no policy, no WS yet.
**Acceptance:**
- subscribe(event_type) returns a token; unsubscribe removes handler.
- publish(event) supports exact type and '*' wildcard subscribers.
- Published events include type, ts_ms, sid (if present), and are safe if a handler throws.

### B1-B: WS Adapter + Gateway
**Files:** app/ws/adapter.py, app/asgi_gateway.py
**Non-goals:** No policy/ASR/TTS logic; adapter only routes I/O.
**Acceptance:**
- Accept only subprotocol 'chat.v2'; otherwise reply 426 with JSON error.
- Ping→Pong round-trip; text frames parsed safely; binary forwarded to engine.
- Clean close with no exceptions; health endpoint `/api/v1/health` returns engine:'v2'.

### B1-C: Engine shell + Minimal Exporter
**Files:** app/voice_v2/engine.py, app/telemetry/exporter.py
**Non-goals:** No policy frames; no ASR/TTS.
**Acceptance:**
- EVT_WS_OPEN/CLOSE and EVT_WS_JSON_* / EVT_WS_AUDIO_* captured under `exports/<sid>/`.
- manifest.json includes sid, start/end timestamps, counts.

