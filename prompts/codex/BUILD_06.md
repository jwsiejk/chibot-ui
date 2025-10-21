# BUILD 06 — Exporter, Packaging, Admin Flow APIs, and Performance Telemetry

**Alignment guard (do not omit):**
- Align with SSOT in `/docs`. Touch only listed files. ≤ 500 LOC/file; ≤ 3 files/task.
- Preserve `chat.v2`; ensure privacy and size controls.

---

### B6-A: File Exporter Writer (ndjson + manifest)
**Files:** `app/telemetry/exporter.py` (update)  
**Non-goals:** DB/OTLP  
**Acceptance:**  
- Writes `events.ndjson` and `manifest.json` with counts/by_type; rotation-safe.

---

### B6-B: Admin Flow Zip
**Files:** `app/admin/flow_zip.py` (new)  
**Non-goals:** UI  
**Acceptance:**  
- Zips `exports/<sid>/` with README.txt summary; returns path/bytes on demand.

---

### B6-C: Export Packaging & Redaction
**Files:** `app/telemetry/exporter.py` (update), `app/admin/flow_zip.py` (update), `docs/20_ARCH_BUILD_ORDER.md` (update)  
**Non-goals:** Full DLP  
**Acceptance:**  
- Deterministic folder structure; SHA256 entries listed in manifest; max zip size cap with truncation notice; redaction pass applied to NDJSON before zip.

---

### B6-D: Admin Flow Trace Reader (API)
**Files:** `app/admin/flow_api.py` (new)  
**Non-goals:** UI  
**Acceptance:**  
- Stream/read latest events by `sid`, filter by type, link to zip endpoint; auth gate reuse from Build 02.5.

---

### B6-E: Performance Telemetry & Budgets
**Files:** `app/voice_v2/engine.py` (update), `app/telemetry/exporter.py` (update), `docs/20_ARCH_BUILD_ORDER.md` (update)  
**Non-goals:** Alerting stack  
**Acceptance:**  
- Record `t_first_partial_ms`, `t_final_ms`, `t_tts_start_ms` per turn; doc SLO budgets and thresholds in SSOT; exporter aggregates simple percentiles per session.

> “Return only diffs for the files listed above. Do not modify or create any other files.”
