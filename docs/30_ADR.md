# Architecture Decision Records (append-only)

- **ADR-0001 (2025-10-21)** — Single-path v2 only: `/ws/v2/chat` (subprotocol `chat.v2`). v1 removed.
- **ADR-0002 (2025-10-21)** — ASR vendors: Deepgram primary, Speechmatics secondary (switchable). Whisper not used.
- **ADR-0003 (2025-10-21)** — Barge-in model: automatic only; toggle `barge_in_enabled` via policy.
- **ADR-0004 (2025-10-21)** — Policy frames must always include: `mode`, `allow_auto_vad`, `barge_in_enabled`, `auto_commit_when_ready`, and `telemetry`.
- **ADR-0005 (2025-10-21)** — ACWR precedence: `effective = policy_state AND admin_switch`. No runtime cfg input.
- **ADR-0006 (2025-10-21)** — Templates root standardized to `app/templates/` only.
- **ADR-0007 (2025-10-21)** — NLU/NLG contracts added; exactly one NLU & one NLG object per turn; persona-driven templates later.
- **ADR-0008 (2025-10-21)** — Telemetry policy block controls runtime logging (enabled, level, categories, redaction, sampling).
- **ADR-0009 (2025-10-21)** — Telemetry Envelope v1 & Redaction: events declare `schema_version: "1"` and follow a stable envelope (`type`, `ts_ms`, optional context fields, `meta`). Optional fields may be added without a new version; changes to required fields demand a new schema_version. The telemetry bus normalizes timestamps/levels and redacts PII/secrets within `meta` (emails, bearer tokens, URL secrets, opaque tokens, and overlong blobs) before dispatching to subscribers to ensure privacy-by-default across engine/exporter/admin consumers.

---

- **ADR-0010 (2025-10-22)** — Exporter is a **telemetry-bus subscriber**  
  Exporter subscribes to the normalized bus (wildcard `"*"` with `sid` filter) and writes **only redacted/normalized** events to disk. Engine must **not** write directly to the exporter. `manifest.json` is written at `begin()` with `open: true`, and atomically finalized at `end()` with `open: false` (tmp + rename). Counters (`events_written`, `by_type`) are updated incrementally for crash tolerance.

- **ADR-0011 (2025-10-22)** — **Admin Flow Trace API** (read-only, bearer-auth)  
  Observability is served via HTTP, not WS:  
  `GET /api/v1/admin/flow/{sid}/trace?type=EVT_X,EVT_Y&since_ms=<ts>&limit=<n>` streams **redacted** NDJSON;  
  `GET /api/v1/admin/flow/{sid}/zip` downloads a packaged, **redacted** archive. Auth is required; endpoints are read-only.

- **ADR-0012 (2025-10-22)** — **Export packaging (deterministic, redacted, capped)**  
  The packaged `flow.zip` contains exactly (in stable lexicographic order):  
  `README.txt`, `events.redacted.ndjson`, `flow_timeline.ndjson`, `manifest.json`, `nlg.ndjson`, `nlu.ndjson`.  
  A redaction pass is re-applied during packaging (idempotent) to produce `events.redacted.ndjson`. `manifest.json` includes `sha256` digests for packaged files. Default cap: **25 MB** total; truncation policy is recorded in `manifest.truncated` with dropped categories/line counts. Previously mentioned artifacts like `server.log`, `ws_in.ndjson`, `ws_out.ndjson` are **not** part of the packaged export (privacy & size).

- **ADR-0013 (2025-10-22)** — **WS backpressure + coalescing semantics**  
  Outbound WS outbox is bounded (size **256**). On overflow, policy is **drop-newest** and emit `EVT_WS_OUTBOX_DROP` to the telemetry bus (telemetry-only; never sent to client). Under backpressure, ASR partials may be coalesced. Server→client **allow-list remains unchanged**.

- **ADR-0014 (2025-10-22)** — **Performance telemetry + SLOs**  
  Per turn we record `t_first_partial_ms`, `t_final_ms`, `t_tts_start_ms`. Engine emits `EVT_PERF_SUMMARY` at `EVT_TURN_END` and the exporter persists a copy under `manifest.summary`. SLO targets/p95: first partial ≤ **450/750 ms**; final ≤ **2000/3000 ms**; TTS start ≤ **350/600 ms**.
