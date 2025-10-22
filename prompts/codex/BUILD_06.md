# BUILD 06 — Exporter, Packaging, Admin Flow APIs, and Performance Telemetry

**Alignment guard (do not omit):**
- Align with SSOT in `/docs`. Touch only listed files. ≤ 500 LOC/file; ≤ 3 files/task.
- Preserve `chat.v2`; ensure privacy and size controls.

---

### B6-A — File Exporter Writer (ndjson + manifest)
**File:** `app/telemetry/exporter.py` (upd)  
**Acceptance:** Writes `events.ndjson` + `manifest.json`; rotation-safe.

---

### B6-B — Admin Flow Zip
**File:** `app/admin/flow_zip.py` (new)  
**Acceptance:** Zips `exports/<sid>/` with README.txt; returns path/bytes.

---

### B6-C — Export Packaging & Redaction
**Files:** `app/telemetry/exporter.py` (upd), `app/admin/flow_zip.py` (upd), `docs/20_ARCH_BUILD_ORDER.md` (upd)  
**Acceptance:** Deterministic structure; SHA256 list in manifest; size cap + truncation note; redaction pass applied before zip.

---

### B6-D — Admin Flow Trace API
**File:** `app/admin/flow_api.py` (new)  
**Acceptance:** Stream/read events by `sid`, filter by type, link to ZIP; reuses auth gate.

---

### B6-E — Performance Telemetry & Budgets
**Files:** `app/voice_v2/engine.py` (upd), `app/telemetry/exporter.py` (upd), `docs/20_ARCH_BUILD_ORDER.md` (upd)  
**Acceptance:** Record `t_first_partial_ms`, `t_final_ms`, `t_tts_start_ms`; doc SLO budgets.

---

### B6-F — Local Tests & Runner (Exporter/Zip/Perf API)
**Files:** `tests/test_exporter_packaging.py` (new), `scripts/run_build06_tests.sh` (new; executable)  
**Acceptance:** Runner executes exporter/zip perf smoke and prints `BUILD_06_TESTS: PASS`.


**Smoke acceptance (minimal trace):**
- Generate an export and verify ZIP contains: `manifest.json`, `server.log`, `ws_in.ndjson`, `ws_out.ndjson`, `flow_timeline.ndjson`, `nlu.ndjson`, `nlg.ndjson`.
- `server.log` shows an applied `EVT_TELEMETRY_POLICY` and at least one `EVT_VENDOR_DEBUG` with sensitive fields redacted per `redaction.py`.

> "Return only diffs for the files listed above. Do not modify or create any other files."

---

## Revision R1 — Build 6 clarifications and updates (2025‑10‑22)

> **This section refines Build‑6 scope while preserving the original text above for historical context.** Where conflicts exist (e.g., packaged file list), this Revision R1 **supersedes** prior acceptance criteria.

### R1‑A — Exporter lifecycle & privacy (supersedes B6‑A details)
- Exporter attaches to the **telemetry bus as a subscriber** (wildcard `"*"` + `sid` filter) and writes **already-normalized, redacted** events to `exports/<sid>/events.ndjson`.  
- **No direct engine→exporter writes.** All disk persistence flows through the bus to ensure uniform redaction.
- `manifest.json` is created at `begin()` with: `sid`, `started_ms`, `open: true`, counters (`events_written`, `by_type`).  
  On `end()`, write `ended_ms`, `open: false`, and atomically replace the manifest (tmp + rename).

**Acceptance (R1):**
- Any bearer token, email, URL secret, or oversize blob present in telemetry appears **masked** in `events.ndjson`.
- After an unclean shutdown mid‑turn, `manifest.json` remains present with `open: true` and non‑negative counters.

---

### R1‑B — Flow ZIP contents & determinism (supersedes B6‑B/B6‑C packaging file set)
`flow.zip` contains **exactly** the following files (stable lexicographic order):

1. `README.txt`
2. `events.redacted.ndjson`
3. `flow_timeline.ndjson`
4. `manifest.json`
5. `nlg.ndjson`
6. `nlu.ndjson`

Notes:
- A **redaction pass** is re‑applied during packaging (idempotent) to produce `events.redacted.ndjson` from `events.ndjson`.
- `flow_timeline.ndjson` is a light view: `EVT_TURN_BEGIN/END`, `EVT_TTS_START/END`, `EVT_TTS_MASK`, `EVT_MIC_GATE`, `EVT_POLICY_APPLIED`, `EVT_BARGE_IN`, and relevant `EVT_WS_*` state signals (not full payloads).
- Previously listed artifacts (`server.log`, `ws_in.ndjson`, `ws_out.ndjson`) are **excluded** from the packaged ZIP to reduce privacy risk and size; keep them internal/ephemeral if needed.

**Determinism & integrity:**
- File order is deterministic; `manifest.json` uses `sort_keys: true`.
- `manifest.sha256` maps each packaged filename → SHA‑256 digest.
- Default cap: **25 MB**. If necessary, drop low‑value streams first (vendor debug → partials → tail of full events). Record truncation in `manifest.truncated` with counts.

**Acceptance (R1):**
- Re‑packaging the same inputs yields **identical ZIP bytes** and matching digests.
- Injected secrets are masked in `events.redacted.ndjson`.  
- Oversize sessions show `manifest.truncated: true` with accurate `dropped` details.

---

### R1‑C — Admin Flow Trace API (clarifies B6‑D)
- `GET /api/v1/admin/flow/{sid}/trace?type=EVT_X,EVT_Y&since_ms=<ts>&limit=<n>`  
  Returns `application/x-ndjson` stream of **redacted** events filtered by `type` and time.
- `GET /api/v1/admin/flow/{sid}/zip`  
  Returns `application/zip` of `flow.zip` defined in R1‑B.
- Both endpoints are **read‑only** and require bearer auth via the existing gate; respond `401` on failure.

**Acceptance (R1):**
- Without `Authorization`, responses are `401` JSON.
- With valid auth, filters apply correctly; ZIP’s SHA‑256 hashes match `manifest.sha256`.

---

### R1‑D — Performance telemetry & SLOs (clarifies B6‑E)
- Engine records per turn:
  - `t_first_partial_ms` (first ASR partial),
  - `t_final_ms` (final ASR),
  - `t_tts_start_ms` (TTS start).
- At `EVT_TURN_END`, emit `EVT_PERF_SUMMARY` and persist a copy under `manifest.summary`.

**SLO targets/p95:**
- First partial ≤ **450 / 750 ms**  
- Final transcript ≤ **2000 / 3000 ms**  
- TTS start ≤ **350 / 600 ms**

**Acceptance (R1):**
- Simulated turn produces all three metrics (non‑negative).  
- `manifest.summary` mirrors the emitted summary.

---

### R1‑E — Tests & runner (clarifies B6‑F)
- `tests/test_exporter_packaging.py`:
  - Asserts ZIP file set equals R1‑B.
  - Validates `sha256` entries.
  - Verifies redaction and truncation reporting.
- `scripts/run_build06_tests.sh`: executes only Build‑6 tests and prints `BUILD_06_TESTS: PASS` on success.

---

### R1‑F — Contract guardrails (non-goals)
- **WS contract remains stable** in Build 6: server→client allow‑list unchanged (`policy.interaction`, `info`, `tts.start`, `tts.end`, `asr.ready`, `asr.partial`, `asr.final`, `error`).
- Backpressure: bounded outbox **256**, **drop-newest**; `EVT_WS_OUTBOX_DROP` is **telemetry-only** (never sent to client).
- No NLU/NLG frames are sent over WS; they are server-side telemetry only.

---

### Appendix — Example `manifest.json` (minimal fields)
```json
{
  "sid": "abc123",
  "schema_version": "1",
  "started_ms": 1732200000000,
  "ended_ms": 1732200065000,
  "open": false,
  "events_written": 4287,
  "by_type": {"EVT_TURN_BEGIN": 12, "EVT_TURN_END": 12, "EVT_TTS_START": 12, "EVT_TTS_END": 12},
  "summary": {"t_first_partial_ms": 410, "t_final_ms": 1820, "t_tts_start_ms": 320},
  "sha256": {
    "README.txt": "…",
    "events.redacted.ndjson": "…",
    "flow_timeline.ndjson": "…",
    "manifest.json": "…",
    "nlg.ndjson": "…",
    "nlu.ndjson": "…"
  },
  "truncated": false
}
