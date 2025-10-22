# BUILD 08 — Cutover, Guards, and CI Gates

**Alignment guard (do not omit):**
- Align with SSOT in `/docs`. Touch only listed files. ≤ 500 LOC/file; ≤ 3 files/task.
- Preserve `chat.v2`; deprecate v1 paths safely.

---

### B8-A — Single-path v2 (Cutover)
**File:** `app/asgi_gateway.py` (upd)  
**Acceptance:** `/ws/v1/chat` returns 410 Gone JSON error; `/ws/v2/chat` is authoritative; info endpoints remain.

---

### B8-B — CI Checks (Schema & Info Gates)
**Files:** `ops/ci_checks.md` (new), `ops/schema_check.py` (new)  
**Acceptance:** Script asserts: (1) `/api/v1/info` responds, (2) sample frames in `docs/10_CONTRACT_WS.md` validate via `app/ws/validator.py`, (3) no references to `/ws/v1/chat` or top-level `/templates`.
