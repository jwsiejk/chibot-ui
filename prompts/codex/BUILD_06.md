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
