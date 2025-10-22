# Architecture Build Order (v2)

> Build sizes are **Codex-friendly** (≤3 files / task, ≤300–500 lines).  
> Every task has explicit **Non-goals** and **Acceptance**.  
> **v2-only**: Do **not** add any legacy v1 routes, flags, or shims.

## Build 1 — Server v2 Bootstrap
- **B1-A: Telemetry Bus + events**  
  **Files:** `app/telemetry/bus.py`, `app/voice_v2/__init__.py`  
  **Non-goals:** No exporter, no policy, no WS send bridge.  
  **Acceptance:** `subscribe(event_type)` returns a token; `unsubscribe` removes handler; `publish(event)` supports exact type and `'*'` wildcard; safe if a handler throws.

- **B1-B: WS Adapter**  
  **Files:** `app/ws/adapter.py`, `app/asgi_gateway.py`  
  **Non-goals:** No legacy paths, no resume.  
  **Acceptance:** require subprotocol **chat.v2**; `ping`→`pong`; clean close; `/api/v1/health` shows `engine:"v2"`.

- **B1-C: Engine shell + minimal exporter**  
  **Files:** `app/voice_v2/engine.py`, `app/telemetry/exporter.py`  
  **Non-goals:** No policy diffs, no mask, no gate.  
  **Acceptance:** EVT_WS_* taps recorded under `exports/<sid>/…`.

## Build 2 — Policy Manager (+ telemetry block)
- **B2-A:** `app/policy/loader.py` adds defaults incl. `telemetry{…}`; doc synced.  
  **Non-goals:** No admin UI, no hot-reload.  
  **Acceptance:** snapshot has 4 core keys + telemetry.

- **B2-B:** `app/voice_v2/policy_manager.py` (precedence = policy_state ∧ admin_switch).  
  **Non-goals:** No persistence.  
  **Acceptance:** diff(prev→next) only changed keys.

- **B2-C:** `engine.py` pushes full `policy.interaction` on open; exporter logs diffs.  
  **Non-goals:** No outbound bridge yet.  
  **Acceptance:** one `EVT_POLICY_APPLIED` with diffs.

- **B2-D:** Breadcrumb `EVT_ACWR_RECOMPUTE {policy_acwr, admin_enabled} -> {effective}`.  
  **Acceptance:** event present with both inputs and effective value.

## Build 3 — TTS Tracker
- **B3-A:** `voice_v2/tts_tracker.py` (start/end/is_active/release_at_ms).  
- **B3-B:** Engine integration → `tts.start … (seconds) … tts.end`; post-hold → idle w/ acwr:true.  
- **B3-C:** Exporter subscribes to `EVT_TTS_*` (log `utt_id`, `post_hold_ms`).  
  **Acceptance:** mask not yet emitted in this build.

## Build 4 — Gate, Barge (auto), Outbound Bridge, Mask, Telemetry Parity — **This build**
- **B4-A:** `voice_v2/gate.py` (gate on/off reasons; respects telemetry).  
  **Acceptance:** `EVT_MIC_GATE` emitted on changes with reasons array.

- **B4-B:** Engine honors `barge_in_enabled` during TTS (ignore vs interrupt).  
  **Acceptance:** barge attempts only while `assistant_speaking`.

- **B4-C:** `EVT_BARGE_IN {source:"auto_vad"|"asr_evidence", granted:true|false, reason?:string}`.  
  **Acceptance:** denied attempts still logged with reason; granted correlates with `ConfirmingBarge→Listening`.

- **B4-D:** Auto Barge Decision regression test added (done).  
  **Acceptance:** test included in Build 04 suite passes.

- **B4-E:** **Outbound WS Bridge** (adapter subscribes to `EVT_WS_JSON_SEND` and forwards **allowed** payloads for its `sid`).  
  **Allow‑list (drop others):** `policy.interaction`, `info`, `tts.start`, `tts.end`, `asr.ready`, `asr.partial`, `asr.final`, `error`  
  **Queue:** bounded (≥256); on overflow, drop‑newest and publish `EVT_WS_OUTBOX_DROP {sid,dropped,now}`  
  **Lifecycle:** subscribe **before** initial policy publish; unsubscribe + cancel sender on **every** close path  
  **Acceptance:** client receives **exactly one** `policy.interaction` on connect; multi‑session isolation (no cross‑sid leakage); drop telemetry increments under stress.

- **B4-F:** **Mask Breadcrumbs** — emit `EVT_TTS_MASK {phase:"engaged"|"cleared"}` on TTS start/end (in addition to TTS + gate).  
  **Acceptance:** order: `engaged` before `cleared`; while engaged, `EVT_MIC_GATE.effective=true` with reason `tts_active`.

- **B4-G:** **Keepalive + Initial Policy Ordering + Isolation**  
  **Deepgram keepalive (provider WS):** send `{"type":"KeepAlive"}` immediately and then every `DG_KEEPALIVE_INTERVAL_S` (env; **default 5.0s**). Stop task cleanly on close.  
  **Server→client keepalive (chat adapter):** send WS ping or `{"type":"keepalive","ts":…}` every `WS_PING_INTERVAL_MS` (env; **default 25000**). Cancel on all close paths.  
  **Initial policy ordering:** adapter subscription is active **before** engine publishes the initial snapshot; exactly **one** `policy.interaction` observed on connect (dedupe if needed).  
  **Multi‑session isolation:** enforce `sid`‑matching in bridge; no cross‑talk.  
  **Acceptance:**  
   • Deepgram: ≥2 keepalives observed within ~11s under default timing (or smaller test override); task stops on close.  
   • Server keepalive: at least one keepalive observed across interval; stops on close.  
   • Initial snapshot delivered once; duplicate publish does not leak a second copy.  
   • Two concurrent sessions receive only their own frames.

- **B4-H:** **Telemetry Parity** — all events normalized to include `"schema_version":"1"` in the centralized bus.  
  **Acceptance:** mixed adapter/engine events observed with `schema_version:"1"`.

- **B4-I:** **Docs** — `doc/10_CONTRACT_WS.md` updated with canonical error taxonomy and v2‑only semantics; `doc/05_REPO_STRUCTURE.prompt.md` synced with actual file names.

## Build 5 — ASR Manager + NLU/NLG seams
- **B5-A:** `voice_v2/asr_manager.py` (Deepgram first): warm_up → `EVT_ASR_READY`; partial/final with `req_id`.  
  **Acceptance:** one FINAL per turn.
- **B5-B:** Speechmatics adapter + selection; identical event shapes.  
  **Acceptance:** vendor parity on `EVT_ASR_*`.
- **B5-C:** NLU hook: after `asr.final`, log **exactly one** NLU per turn.  
  **Acceptance:** `EVT_NLU` present once with `req_id`.
- **B5-D:** Dialog Policy + NLG hook: log decision + **exactly one** NLG per turn.  
  **Acceptance:** `EVT_POLICY_DECISION`, `EVT_NLG` present once; `req_id` consistent.
- **B5-E:** Audio Envelope & Jitter Buffer (binary sequencing window, gap behavior).  
  **Acceptance:** window documented in contract; tests cover out‑of‑order frames.
- **B5-F:** Provider Interfaces & Circuit Breakers (open/trip/close breadcrumbs, no secrets in telemetry).  
  **Acceptance:** `EVT_PROVIDER_OPEN|TRIP|CLOSE` emitted; secrets redacted.

## ## Build 6 — Exporter, Packaging, Admin Flow, Perf Telemetry

> **Goal:** Make observability safe, deterministic, and self-serve without changing the WS contract.  
> **Scope:** B6‑A … B6‑F. **Non‑goals:** New client WS message types; provider integrations (moved to Build 5).

### B6‑A — File Exporter (ndjson + manifest; bus subscriber)
- Attach the exporter as a **bus subscriber** to receive **normalized/redacted** events for the current `sid`.  
- Remove direct engine→exporter writes.  
- `begin(sid)` writes an initial `manifest.json` with `{{ "sid": "...", "started_ms": 0, "open": true, "events_written": 0, "by_type": {{}} }}`.  
- `end(sid)` atomically updates `manifest.json` (`open:false`, `ended_ms`) and flushes counters.

**Acceptance**
- Every line in `exports/<sid>/events.ndjson` is redacted (e.g., bearer tokens masked).  
- After a crash mid‑session, `manifest.json` exists with `open:true`; on next close, it becomes `open:false` with a valid `ended_ms`.

### B6‑B — Admin Flow Zip (deterministic)
- `build_flow_zip(sid)` produces `exports/<sid>/flow.zip` with a **stable file set and order**:
  - `manifest.json`, `events.redacted.ndjson`, `flow_timeline.ndjson`, `nlu.ndjson`, `nlg.ndjson`, `README.txt`.
- Include `sha256` digests for all packaged files in `manifest.json["sha256"]`.

**Acceptance**
- Repeated packaging of the same session yields identical zip bytes and matching `sha256` entries.

### B6‑C — Export packaging policy (redaction, size cap, truncation)
- Re‑apply redaction to generate `events.redacted.ndjson` (idempotent).  
- Cap total zip size (default **25 MB**) and per‑file line counts; if needed, drop in this order: vendor debug → partials → tail of full events.  
- Record truncation under `manifest.truncated`.

**Acceptance**
- A synthetic session exceeding caps yields `manifest.truncated == true` with accurate drop counters.

### B6‑D — Admin Flow Trace API (read‑only, auth‑gated)
- `GET /api/v1/admin/flow/{{sid}}/trace?type=EVT_X,EVT_Y&since_ms&limit` → **application/x-ndjson** of redacted events.  
- `GET /api/v1/admin/flow/{{sid}}/zip` → **application/zip**.  
- Reuse existing bearer **authorize()**; 401 on failure.

**Acceptance**
- Without auth: 401 JSON. With auth: streaming NDJSON filter works; `/zip` returns a valid package whose digests match `manifest.sha256`.

### B6‑E — Performance telemetry & budgets
- Record per‑turn metrics: `t_first_partial_ms`, `t_final_ms`, `t_tts_start_ms`.  
- Emit `EVT_PERF_SUMMARY` at `EVT_TURN_END`.  
- Save the same summary under `manifest.summary`.  
- **Document SLOs** (targets/p95): first partial ≤ **450 ms** / **750 ms**, final ≤ **2.0 s** / **3.0 s**, TTS start ≤ **350 ms** / **600 ms**.

**Acceptance**
- Unit tests assert all three timings are present and non‑negative; `manifest.summary` mirrors `EVT_PERF_SUMMARY`.

### B6‑F — Tests & runner
- `tests/test_exporter_packaging.py` covers: redaction, packaging file set/order, SHA‑256 map, size‑cap truncation, perf summary.  
- `scripts/run_build06_tests.sh` runs only Build‑6 tests and prints `BUILD_06_TESTS: PASS` on success.

### Contract notes (WS remains stable)
- **No new WS message types** are introduced in Build 6.  
- Outbound WS adapter continues to enforce an **allow‑list**: `policy.interaction`, `info`, `tts.start`, `tts.end`, `asr.ready`, `asr.partial`, `asr.final`, `error`.  
- Backpressure policy: bounded outbox (256), **drop‑newest**; telemetry `EVT_WS_OUTBOX_DROP` is recorded (not sent).

*Section last updated:* 2025-10-22T07:22:02Z


## Build 7 — Client v2 Minimal
- **C7-A:** WS layer + PolicyBus (ACWR stickiness if omitted).  
  **Acceptance:** Policy snapshot reflected in UI state.
- **C7-B:** Waveform + state badges (reflect policy & TTS in real time).  
  **Acceptance:** badges track `EVT_TTS_MASK` and engine state.
- **C7-C:** Playback truth + auto barge (onplay/onended; client telemetry).  
  **Acceptance:** emits `EVT_PLAYBACK start/end` with media_id when available.
- **C7-D:** Recorder + sender (format header; partial/final round‑trip).  
  **Acceptance:** end‑to‑end user turn flows produce NLU/NLG/tts.
- **C7-E:** Recorder Contract & StartOnce.  
  **Acceptance:** idempotent start; rejects double‑start with local error.
- **C7-F:** Reconnect UX + Resume (`client.resume` token).  
  **Acceptance:** reconnect within 10s resumes policy + state; else clean new session.
- **B7-E:** Policy Inspector (Read‑only).  
  **Acceptance:** shows active policy snapshot + diffs over time.

## Build 8 — Contract Guardrails & CI
- **B8-A: Path/Subprotocol/Auth enforcement tests**
  - `/ws/v2/chat` succeeds; any other WS path → **HTTP 404**.
  - Missing/wrong subprotocol → **HTTP 426**.
  - Missing/invalid auth (pre‑upgrade) → **HTTP 401** with `{"type":"error","code":"unauthorized"}`.
- **B8-B: CI grep guardrails**
  - Fail PR if any source contains:
    - `ws/v1/` or `chat.v1`
    - `410 Gone` (WS deprecation)
    - `legacy`, `compat`, or `migration` in the context of earlier versions
- **B8-C: Error taxonomy conformance tests**
  - Assert JSON error frame is sent **before** WS close.
  - Codes: `unknown_type`→1003, `bad_json`→1003, `frame_too_large`→1009, `audio_not_expected`→1003, **rate_limited**→**1013**, bad UTF‑8→1007.

### NLU/NLG wiring (timeline)
- Wire NLU after **Build 5** (`asr.final`); log one NLU/turn.
- Add NLG in **Build 5**; log one NLG/turn.
- Exporter captures both in their own streams.
