# Architecture Build Order (v2)

> Build sizes are **Codex-friendly** (≤3 files / task, ≤300–500 lines).  
> Every task has explicit **Non-goals** and **Acceptance**.  
> **v2-only**: Do **not** add any legacy v1 routes, flags, or shims.

## Build 4 — Gate & Barge (auto only) — **This build**
- **Outbound WS Bridge**: Adapter subscribes to `EVT_WS_JSON_SEND`, forwards **allowed** payloads for its `sid` only, via a bounded outbox queue and background sender task.
  - **Allow‑list** now: `{"policy.interaction", "info", "tts.start", "tts.end", "asr.ready", "asr.partial", "asr.final", "error"}`
  - **Queue**: bounded (e.g., 256); drop‐newest on overflow; emit `EVT_WS_OUTBOX_DROP`.
  - **Lifecycle**: Subscribe **before** engine publishes initial snapshot; unsubscribe + cancel sender task on **every** close path.
  - **Ping**: Server emits periodic WS `ping` as keepalive.
  - **Acceptance:** Client receives **exactly one** `policy.interaction` on connect; multi‑session isolation (no cross‑sid leakage); drop telemetry increments under stress.

- **Mask Breadcrumbs**: Emit `EVT_TTS_MASK {phase:"engaged"|"cleared"}` on TTS start/end (in addition to existing TTS + gate events).
  - **Acceptance:** Order: `engaged` before `cleared`; while engaged, `EVT_MIC_GATE.effective=true` with reason `tts_active`.

- **Telemetry Parity**: All events normalized to include `"schema_version":"1"` (centralized in the bus).
  - **Acceptance:** Mixed events from adapter/engine observed with `schema_version:"1"`.

- **Docs**: `doc/10_CONTRACT_WS.md` updated with canonical error taxonomy and v2‑only semantics; `doc/05_REPO_STRUCTURE.prompt.md` synced with actual file names.

## Notes for later builds
- Build 5: `req_id` minted at `EVT_TURN_BEGIN` and propagated (ASR→NLU→NLG).
- Build 7: Consider adding `seq` to server→client frames for resume.

