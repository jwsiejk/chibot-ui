# Deepgram STT Migration Plan (AskChip)

## 1) Executive Summary

**Current STT vendor & approach (repo-derived):** AskChip uses Google Cloud Speech-to-Text streaming. The WebSocket adapter (`app/ws/adapter.py`) instantiates `GCPStreamingASREngine` (`app/services/asr/gcp_engine.py`) and streams PCM16 audio over a live GCP StreamingRecognize session. The ASR results are emitted as `asr.partial`/`asr.final` events and routed into the policy/engine and UI transcript bridge.

**Target:** Deepgram Streaming STT.

**Migration strategy:** Phased, gated, and rollbackable (feature flag + vendor selector), with shadow/dual-run optional and minimal/no protocol changes on `/ws/v2/chat` (`app/asgi_gateway.py`).

## 2) Current-State STT Architecture (Repo-derived)

### Repo Findings (Search / Discovery Checklist)

| Area | Primary file(s) | Purpose / notes |
| --- | --- | --- |
| WS entrypoint | `app/asgi_gateway.py` | Defines `WS_ROUTE = "/ws/v2/chat"` and accepts websocket connections on that route. |
| WebSocket adapter | `app/ws/adapter.py` | Core WS handler: receives text/binary frames, manages ASR session lifecycle, emits `asr.ready`, `asr.partial`, `asr.final`, and forwards audio frames. |
| ASR engine implementation | `app/services/asr/gcp_engine.py` | Implements `GCPStreamingASREngine` using `google.cloud.speech.SpeechClient` streaming API. |
| Audio ingress (client) | `app/static/js/audio/ws_audio_runtime.js` | Sends PCM audio; emits `client.turn_start` when speech is first detected (VAD). |
| Transcript UI bridge | `app/static/js/ws/transcript_bridge.js` | Handles `asr.partial` and `asr.final` events; partials render live text; finals are stored for later user-turn reconciliation. |
| Config/env | `app/config.py` | STT defaults and flags (`GCP_STT_DEFAULT_SAMPLE_RATE`, `GCP_STT_DEFAULT_LANGUAGE`, `ASR_TRACE`, placeholders for Deepgram). |
| Dependencies | `requirements.txt` | Includes `google-cloud-speech` for GCP streaming. |

**Current STT vendor and how it’s instantiated**
- Vendor: **GCP Streaming Speech-to-Text**.
- Instantiation: `ChatV2Adapter._create_asr_engine()` returns `GCPStreamingASREngine()` (`app/ws/adapter.py` → `app/services/asr/gcp_engine.py`).
- GCP client: `speech.SpeechClient()` created inside `GCPStreamingASREngine.__init__`.

**Current lifecycle: open/close/keepalive/timeout**
- **Open triggers:**
  - Server accepts `client.turn_start` from the client (`app/ws/adapter.py`), marks `current_turn_open`, and schedules `_open_asr()` if not already open.
  - On the first PCM chunk, if no stream exists and a turn is open, the adapter schedules ASR open (`_handle_binary()` in `app/ws/adapter.py`).
- **Keepalive behavior:**
  - WebSocket keepalive is managed by the adapter (`_start_server_keepalive`), and audio keepalive frames are handled in client audio code. There is no explicit GCP streaming keepalive; the stream is kept open until explicitly closed.
- **Close triggers:**
  - `client.turn_stop` closes the ASR stream if open (adapter `_handle_text` → `_close_asr`).
  - Final transcription path defers closing to policy (adapter `_handle_asr_result` invokes engine and may close in subsequent flow).
  - Transport close (`websocket.disconnect`) triggers `_close_asr(..., reason="transport_closed")`.
- **Timeout behavior:**
  - GCP engine treats `OutOfRange: Audio Timeout Error` as a soft end-of-turn, logs `evt=asr_timeout`, and can promote a final transcript (`app/services/asr/gcp_engine.py`).
  - Adapter-level no-audio safety net monitors for silent sessions and can close/alert (`_refresh_no_audio_safety_net`, `evt=asr_no_audio_after_header` in `app/ws/adapter.py`).

**Current message/event schema between server ↔ client**
- **Client → server:**
  - `client.turn_start` / `client.turn_stop` control frames (`app/ws/adapter.py`; sent by `app/static/js/audio/ws_audio_runtime.js`).
  - Binary audio frames (PCM16) over the same `/ws/v2/chat` socket.
- **Server → client:**
  - Allowed event types include `asr.ready`, `input.start`, `asr.partial`, `asr.final`, etc. (see `_OUTBOUND_ALLOWED_TYPES` in `app/ws/adapter.py`).
- **Transcript consumption:**
  - `asr.partial` is rendered live; `asr.final` is stored for reconciliation with `user.turn` messages in `app/static/js/ws/transcript_bridge.js`.

### Audio ingress path (client → WS → server)
1. Browser captures PCM audio and sends binary frames to `/ws/v2/chat` (`app/static/js/audio/ws_audio_runtime.js`).
2. The WS adapter receives binary frames in `_handle_binary()` (`app/ws/adapter.py`), buffers them, and emits them to the audio bridge if gates allow.
3. The adapter forwards audio to the ASR engine once `asr_state` is open (`_emit_audio_chunk` → `_forward_audio_chunk`).

### Transcript emission path (partials/finals → engine → UI)
1. GCP stream responses are converted to `transcript` + `is_final` (`app/services/asr/gcp_engine.py`).
2. Adapter `_handle_asr_result()` publishes `asr.partial`/`asr.final` to the telemetry bus and forwards finals/partials into the engine (`on_asr_partial`, `on_asr_final`) (`app/ws/adapter.py`).
3. Client `transcript_bridge` handles `asr.partial`/`asr.final` and updates UI (`app/static/js/ws/transcript_bridge.js`).

## 3) Target Deepgram STT Architecture

### Proposed module placement (repo-consistent)
- **New adapter/engine module:** `app/services/asr/deepgram_engine.py`
  - Mirror `GCPStreamingASREngine` structure and implement the same `ASREngine` interface (`open`, `write`, `close`).
- **Engine selection:** extend `ChatV2Adapter._create_asr_engine()` to select vendor via config or policy and instantiate `DeepgramStreamingASREngine` when enabled.

### Contract the Deepgram adapter must satisfy
- **Inputs:** PCM16, 16 kHz, mono audio frames (same as current GCP path enforced in `app/ws/adapter.py`).
- **Outputs:** normalized events identical to current pipeline:
  - Partial results → `asr.partial` events.
  - Final results → `asr.final` events.
- **Metadata:** include `vendor: "deepgram"`, `stream_id`, `req_id`, and `partial_seq` as currently used for UI and policy integration.

### Session/turn mapping
- Preserve current stream-to-turn mapping:
  - `client.turn_start` opens an ASR stream (or schedules open).
  - `client.turn_stop` closes that stream (or “soft closes” after finalization).
- Keep `ctx.asr_stream_id` as the per-turn stream identifier, with the Deepgram stream tied to that ID.

## 4) Compatibility Goals / Non-Goals

### Goals
- **Keep `/ws/v2/chat` stable** (`app/asgi_gateway.py`).
- **Preserve event shapes** for `asr.ready`, `asr.partial`, `asr.final`, `input.start` (see `app/ws/adapter.py` and `app/static/js/ws/transcript_bridge.js`).
- **Maintain PCM16 16k mono pipeline** (same defaults and policy overrides in `app/config.py` and `app/ws/adapter.py`).

### Non-Goals
- No UI rework or transcript formatting changes in this migration phase.
- No policy-engine changes to turn lifecycle beyond vendor selection/flagging.

### Expected changes (explicit)
- **New vendor identifier** `vendor: "deepgram"` on emitted ASR events.
- **New config flags and env vars** (see below).
- Optional **new telemetry events** for Deepgram-specific monitoring.

## 5) Required Config / Secrets

### New/updated env vars
- `DEEPGRAM_API_KEY` (string) — Deepgram auth token.
- `DEEPGRAM_STT_MODEL` (string, default: Deepgram default model, e.g., `nova-2`).
- `DEEPGRAM_STT_LANGUAGE` (string, default: `en-US`).
- `DEEPGRAM_STT_SAMPLE_RATE` (int, default: `16000`).
- `DEEPGRAM_STT_ENDPOINTING_MS` (int, optional).
- `DEEPGRAM_STT_INTERIM_RESULTS` (bool, default: `true`).
- **Feature flag / vendor selector:**
  - `ASR_VENDOR` (e.g., `gcp|deepgram`) or `DEEPGRAM_STT_ENABLED` (bool).

### Config loading pattern (repo-aligned)
- Use `app/config.py` helpers (`env_bool`, `_env_int`, `os.getenv`) for defaults and environment overrides.
- Add new config constants alongside existing GCP defaults (`GCP_STT_DEFAULT_SAMPLE_RATE`, `GCP_STT_DEFAULT_LANGUAGE`).
- Preserve existing placeholders (`DEEPGRAM_API_KEY`, `ASR_DEEPGRAM_ENABLED`) but convert them to real configuration values to avoid unused placeholders.

### Safe defaults
- Default to **GCP** vendor unless feature flag explicitly enables Deepgram.
- Default Deepgram model/language/sample rate to match existing GCP defaults to reduce behavioral drift.

## 6) Phased Migration Plan (with Feature Flags + Rollback)

> **Note:** Each phase must be behind a runtime-configurable flag and allow immediate rollback without redeploy if possible.

### Phase 0 — Plumbing & Flag
**Files to change**
- `app/config.py` (add Deepgram envs + vendor selection)
- `app/ws/adapter.py` (vendor selection scaffold)
- Optional: `app/services/asr/__init__.py` (exports)

**Implementation**
- Add `ASR_VENDOR` or `DEEPGRAM_STT_ENABLED` to choose between `gcp` and `deepgram`.
- Add placeholder class `DeepgramStreamingASREngine` with no-op or stubbed methods.
- Wire selection in `_create_asr_engine()` without changing default behavior.

**Acceptance criteria**
- GCP behavior unchanged with flag off.
- No new runtime errors; existing tests pass.

**Rollback**
- Set `ASR_VENDOR=gcp` (or `DEEPGRAM_STT_ENABLED=false`).

### Phase 1 — Adapter Implementation + Unit Tests
**Files to change**
- `app/services/asr/deepgram_engine.py` (new)
- `app/services/asr/gcp_engine.py` (only if interface needs small adjustments)
- `tests/...` (new unit tests)

**Implementation**
- Implement Deepgram streaming client with the same `ASREngine` interface (`open`, `write`, `close`).
- Map Deepgram responses to `transcript` + `is_final` and call adapter result callback.
- Add structured logs mirroring current GCP logs: `evt=asr_open vendor=deepgram`, `evt=asr_partial`, `evt=asr_final`, `evt=asr_close`.

**Acceptance criteria**
- Unit tests validate:
  - open/write/close lifecycle
  - partial/final transcript mapping
  - timeout/idle behavior
- Flagged Deepgram adapter works in isolation.

**Rollback**
- Disable Deepgram flag → revert to GCP.

### Phase 2 — Dual-Run / Shadow Mode (if feasible)
**Files to change**
- `app/ws/adapter.py` (duplicate audio routing and result capture)
- `app/services/asr/deepgram_engine.py`
- `app/services/asr/gcp_engine.py` (if shared abstractions)
- `app/config.py` (shadow mode flag)

**Implementation**
- Keep GCP as primary; send audio to Deepgram in parallel (no user-visible events).
- Compare transcript quality/latency metrics (log side-by-side without emitting to UI).

**Acceptance criteria**
- Deepgram latency and transcript quality within target thresholds (define in metrics: time to first partial, final accuracy).
- No increased error rates or regressions.

**Rollback**
- Disable shadow flag; revert to single-vendor GCP.

### Phase 3 — Limited Rollout
**Files to change**
- `app/ws/adapter.py` (vendor selection at session scope)
- `app/config.py` (percentage/allowlist controls)
- `app/static/js/...` (if telemetry needs client tags)

**Implementation**
- Enable Deepgram for a subset of sessions/users (e.g., by header, allowlist, percentage).
- Ensure runtime logs include session IDs and vendor selection reasons.

**Acceptance criteria**
- Stable error rates; no regressions in ASR readiness or turn handling.
- Latency and transcript quality acceptable for the subset.

**Rollback**
- Switch flag to 0% or `gcp` default.

### Phase 4 — Default On + Cleanup
**Files to change**
- `app/config.py` (default vendor)
- `app/ws/adapter.py` (remove GCP-only assumptions)
- `requirements.txt` (optional: remove GCP dependency if unused)

**Implementation**
- Make Deepgram the default vendor.
- Remove dead vendor-specific logic where safe (e.g., hard-coded GCP checks).

**Acceptance criteria**
- Deepgram is default for all sessions.
- GCP code paths either removed or gated for legacy fallback.

**Rollback**
- Re-enable GCP in config or revert deployment to previous build.

## 6.5) Phase 2 Acceptance Metrics (Shsdow / Dual-Run)

Purpose: Define objective, quantitative criteria for deciding when Deepgram STT is safe to promote beyond shadow mode.

When running Deepgram in shadow or limited rollout mode, evaluate against the following metrics, computed per turn and aggregated per environment.

Latency Metrics

Measured from first PCM frame forwarded to vendor:

Time to first partial

p50 ≤ 300 ms

p95 ≤ 700 ms

Time to final transcript

p50 ≤ 1200 ms

p95 ≤ 2500 ms

Stability Metrics

Stream open failure rate: < 0.5% of turns

Mid-stream disconnect rate: < 0.2% of turns

No-audio / idle timeout events:

Must not exceed baseline observed with GCP in equivalent traffic

Transcript Quality Proxies

Because automated WER may not be available initially:

Empty final transcript rate: < 1% of turns

Final after partial regression: < 0.5%
(cases where final text is shorter or lower confidence than the last partial without policy justification)

Promotion Rule

Deepgram may advance from Phase 2 → Phase 3 when all of the above thresholds are met for:

≥ 500 turns, and

≥ 24 continuous hours without regressions.

## 7) WS Protocol / Event Schema Changes (If Any)

**No protocol change required** for `/ws/v2/chat`. Existing messages (`client.turn_start`, `client.turn_stop`, `asr.ready`, `asr.partial`, `asr.final`) remain in place (`app/ws/adapter.py`, `app/static/js/audio/ws_audio_runtime.js`, `app/static/js/ws/transcript_bridge.js`).

If Deepgram requires explicit turn boundaries beyond current `client.turn_start` / `client.turn_stop`, define new control frames in the same envelope:
- `client.turn_start` (existing): `{ type, turn_id, lane, pre_roll_ms }`
- `client.turn_stop` (existing): `{ type, turn_id, lane }

## 7.1) Deepgram Streaming and Endpoint Semantics

Goal: Make turn boundaries deterministic and prevent “double-final” or premature close issues.

Interim Results

Interim (partial) results: enabled by default

Partial transcripts are treated as replace-latest, not append.

Finalization Rules

The system treats a turn as finalized when either condition occurs:

Client-driven finalization

client.turn_stop is received

Adapter flushes audio and closes the Deepgram stream

Vendor-driven finalization

Deepgram emits a final transcript before client.turn_stop

Adapter:

emits asr.final

marks turn finalized

ignores subsequent vendor partials for that turn

still accepts client.turn_stop as a no-op close

Conflict Resolution

If both happen:

The first final wins

Duplicate finals are logged and dropped:

evt=asr_duplicate_final_dropped vendor=deepgram

## 7.2) Transcription Normalization Rules

Purpose: Ensure Deepgram transcripts behave identically to existing UI and policy assumptions.

Normalization Pipeline

All Deepgram transcripts must pass through the same normalization layer used by GCP results.

Rules:

Casing: sentence-case (first letter capitalized)

Whitespace: trim leading/trailing whitespace

Punctuation: preserve vendor punctuation; do not inject punctuation server-side

Partials:

overwrite previous partial for the same turn

never appended

Finals:

replace any existing partial

become immutable once emitted

Empty finals:

allowed only if user speech duration < silence threshold

otherwise logged as:

evt=asr_empty_final vendor=deepgram

Confidence Handling

If Deepgram confidence is available:

Pass through as metadata

Do not gate UI rendering on confidence during this migration

## 8) Observability / Logging Plan

Add structured logs (mirroring current GCP logs in `app/services/asr/gcp_engine.py` and adapter logs in `app/ws/adapter.py`):

- **Stream lifecycle**
  - `evt=asr_open vendor=deepgram sid=...`
  - `evt=asr_close vendor=deepgram sid=... reason=...`
- **Data flow**
  - `evt=asr_bytes_to_vendor_summary vendor=deepgram bytes_from_bridge=... outcome=...`
- **Latency**
  - time to first partial/final (from open or first audio frame)
- **Errors**
  - `evt=asr_error vendor=deepgram ...`
- **Idle/no audio**
  - `evt=asr_no_audio_before_timeout vendor=deepgram ...`
- **Client log correlation**
  - Include `sid`, `turn_id`, `req_id` in logs as currently done in `_handle_asr_result` (`app/ws/adapter.py`).

## 8.1) Deployment and Dependency Pre-Requisites
Python Dependencies
Choose one approach and document it explicitly in requirements.txt:

Option A — Deepgram SDK

Add:

deepgram-sdk


Option B — Raw WebSocket / HTTP

Add:

websockets
httpx


The adapter should abstract vendor transport so either option can be swapped without touching ChatV2Adapter.

Network / Infra Requirements

Outbound WebSocket or HTTPS access to Deepgram endpoints

Ensure egress policies allow persistent streaming connections

Validate proxy/load balancer idle timeout ≥ 90s

Secrets Handling

DEEPGRAM_API_KEY must be present in:

local .env

staging secrets

production secrets

No fallback to hard-coded values under any circumstances

## 9) Test Plan

### Unit tests
- Deepgram engine lifecycle tests (open → write → close).
- Partial/final mapping tests using mocked Deepgram responses.

### Integration tests
- WebSocket audio streaming integration tests on `/ws/v2/chat`:
  - Validate `asr.ready`/`input.start` are sent and audio is accepted.
  - Validate `asr.partial`/`asr.final` emitted and pass through to UI bridge.

### Manual test scripts
- **Silent user after greet:** verify no-audio safety net and no false finals.
- **Short utterance (first-word clipping):** verify early partial + final accuracy.
- **Long utterance:** ensure stream stays open and final closes cleanly.
- **Network drop:** confirm graceful close and vendor error logging.
- **Barge-in (if supported):** verify no regression in mask/turn handling.

## 10) Risk Register + Mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Audio format mismatch | No transcription or garbled results | Enforce PCM16 16k mono pipeline in adapter; add upfront validation and logs. |
| Partial transcript semantics differ | UI jitter or premature finals | Normalize partial/final handling to current event shapes and debouncing in `transcript_bridge`. |
| Timeout behavior changes | Unexpected turn ends | Mirror GCP timeout handling and add safety nets for idle audio. |
| Vendor reconnect strategy | Dropped sessions | Implement retry/backoff for Deepgram stream open; guard with feature flag. |
| Cost/usage spikes | Increased bill or rate limits | Add usage counters and alerting on bytes/sessions; shadow mode first. |

## Implementation Checklist

- [ ] Add Deepgram config vars to `app/config.py` and document defaults.
- [ ] Implement `app/services/asr/deepgram_engine.py` with the `ASREngine` interface.
- [ ] Extend `ChatV2Adapter._create_asr_engine()` to select vendor by flag.
- [ ] Add structured logging for Deepgram lifecycle and errors.
- [ ] Add unit tests for Deepgram adapter mapping and lifecycle.
- [ ] Add WS integration test for audio streaming → ASR events.
- [ ] Implement shadow mode (if feasible) and compare metrics.
- [ ] Roll out behind feature flag and add rollback switch.

## 10.1) Failure Modes and Fsllbsck Behavior
Scenario	System Behavior	User Impact	Logging
Deepgram stream open fails	Abort turn, emit recoverable error	User retries naturally	evt=asr_open_failed vendor=deepgram
Deepgram drops mid-stream	Close turn, promote partial if present	Slight truncation possible	evt=asr_stream_dropped vendor=deepgram
Vendor final arrives early	Finalize turn immediately	Faster response	evt=asr_final_early vendor=deepgram
No audio after turn_start	Close turn safely	No transcript	evt=asr_no_audio vendor=deepgram
Repeated vendor failures	Optional fallback to GCP (if enabled)	Transparent recovery	evt=asr_fallback_to_gcp
Fallback Policy (Optional)

If ASR_VENDOR_FALLBACK_ENABLED=true:

After N consecutive Deepgram failures in a session:

switch vendor to GCP for remainder of session

log explicit vendor switch event
