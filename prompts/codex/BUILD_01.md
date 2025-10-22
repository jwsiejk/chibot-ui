# BUILD 01 — Server v2 Bootstrap

**Alignment guard (do not omit):**
- Align with SSOT in `/docs` (`00_CONTEXT.md`, `10_CONTRACT_WS.md`, `20_ARCH_BUILD_ORDER.md`, `30_ADR.md`).
- Touch only the files listed per task. Do **not** rename routes, env vars, or policy keys.
- Keep each new/changed file ≤ **500 lines**; ≤ **3 files per task**.
- Preserve the `chat.v2` WebSocket contract and telemetry envelope (error frames use `detail`; ws taps use `meta.ws.{dir,size,preview}`).

---

### B1-A — Telemetry Bus + Event Constants
**Files:** `app/telemetry/bus.py` (new), `app/voice_v2/__init__.py` (new or update)  
**Non-goals:** Exporters, WS code, policy/ASR/TTS, DB/ENV, external deps  
**Acceptance:**  
- `subscribe(event_type)` returns unique token; `unsubscribe(token)` works.  
- `publish(event)` normalizes envelope (`type`, default `ts_ms`, default `level="debug"`), dispatches to exact + `"*"` subscribers, isolates handler exceptions.  
- Envelope supports: `sid, turn_id, who, source, level, meta`.  
- Event constants defined for WS, TTS, Policy, Gate, ASR/NLU/NLG and exported via `__all__`.
- No side effects on import; typed, documented; ≤ 300 LOC per file.

---

### B1-B — WebSocket Adapter v2 (chat.v2) Handshake & Frame Router
**Files:** `app/ws/adapter.py` (new), `app/asgi_gateway.py` (new or update)  
**Non-goals:** ASR/TTS/NLU/NLG/policy logic; exporter persistence; DB/ENV; binary decoding; UI beyond health  
**Acceptance:**  
- Route `/ws/v2/chat`; require subprotocol **chat.v2**; otherwise HTTP **426** JSON `{ "type":"error","code":"bad_subprotocol","detail":"use chat.v2" }`.  
- On upgrade, generate `sid`; publish `EVT_WS_OPEN/EVT_WS_CLOSE`; taps `EVT_WS_JSON_RECV/SEND`, `EVT_WS_AUDIO_RECV/SEND` with `meta.ws.dir/size` and per-conn `seq` for binary.  
- JSON validation: bad JSON → error frame `{type:"error",code:"bad_json",detail:"..."}` (no drop). Unknown type → `{type:"error",code:"unknown_type",detail:"..."}`.  
- Frame size guards: text 64KB, binary 2MB → oversize error + close 1009.  
- Ping/Pong supported; clean close.  
- `/api/v1/health` returns `{"ok":true,"engine":"v2","ws_subprotocol":"chat.v2"}`.

---

### B1-C — Engine v2 Shell + Minimal File Exporter (Session Capture)
**Files:** `app/voice_v2/engine.py` (new), `app/telemetry/exporter.py` (new)  
**Non-goals:** Policy frames/diffs; ASR/LLM/TTS; DB/ENV; compression/cloud upload; UI  
**Requirements:**  
- `EngineV2` exposes `on_open/on_json/on_audio/on_close`, each publishes telemetry (WS_* events) then writes normalized events to exporter.  
- `FileExporter.begin/write/end` → `exports/<sid>/events.ndjson` (NDJSON) and `manifest.json` with counts, first/last ts; rotation-safe.  
- Adapter wires `begin` on open and `end` on close with summary `{close_code}`.  
**Acceptance:** The four hooks generate proper EVT_WS_* lines and a manifest with sane timestamps; exporter tolerates restarts.  

---

### B1-D — Telemetry Envelope v1 + Redaction Rules (Governance)
**Files:** `docs/30_ADR.md` (ADR entry), `docs/10_CONTRACT_WS.md` (update), `app/telemetry/bus.py` (update)  
**Non-goals:** Full DLP, DB retention, admin UI  
**Requirements:**  
- Document `schema_version:"1"` and compatibility policy.  
- Bus redacts **meta-only** strings: emails (`***@domain`), bearer tokens (`****last4`), secret-ish 32–64 char tokens (`abc…xyz`), URL params (`token|key|sig|secret|auth`), very long blobs.  
- Deterministic, linear-time; exceptions logged but not fatal.  
**Acceptance:** Defaults (`ts_ms`,`level`) filled; redaction applied before subscribers/exporter; docs updated.

---

### B1-E — Health / Live / Ready / Info Endpoints
**File:** `app/asgi_gateway.py` (update)  
**Non-goals:** Auth, DB/vendor checks  
**Requirements:**  
- `/api/v1/health` → `{"ok":true,"engine":"v2","ws_subprotocol":"chat.v2"}`  
- `/api/v1/live` → `{"ok":true,"ts_ms":<now_ms>}`  
- `/api/v1/ready` → 200 if `exports/` writable else 503 `{ok:false,reason:"export_path_unwritable"}`  
- `/api/v1/info` → `{"version":"...","git_sha":"...","built_at":"<iso8601>"}`  
**Acceptance:** Endpoints return exact JSON shapes and correct status codes; no side effects on import.

---

### T1 — Local-Only Smoke Tests & Runner (Build 01)
**Files:** `tests/test_bus_publish_basics.py` (new), `tests/test_bus_redaction.py` (new), `scripts/run_build01_tests.sh` (new; executable)  
**Acceptance:**  
- Runner sets `PYTHONPATH=.` then executes:  
  `python -m unittest -v tests.test_bus_publish_basics tests.test_bus_redaction`  
- Prints `BUILD_01_TESTS: PASS` on success (non-zero on fail).

> “Return only the diffs for the files listed above. Do not modify or create any other files.”
