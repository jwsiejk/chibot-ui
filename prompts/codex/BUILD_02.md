# BUILD 02 — Policy Manager & Application (with Telemetry Block)

**Alignment guard (do not omit):**
- Align with SSOT in `/docs` (`00_CONTEXT.md`, `10_CONTRACT_WS.md`, `15_NLU_NLG.md`, `20_ARCH_BUILD_ORDER.md`, `30_ADR.md`).
- Touch only the files listed per task. Do **not** rename routes, env vars, or policy keys.
- Keep each new/changed file ≤ **500 lines**; ≤ **3 files per task**.
- Preserve the `chat.v2` contract and telemetry envelope (error frame uses `detail`; ws taps use `meta.ws.{dir,size,preview}`).

---

### B2-A — Policy Defaults incl. Telemetry
**Files:** `app/policy/loader.py` (new), `docs/10_CONTRACT_WS.md` (examples update)  
**Non-goals:** Engine wiring; admin UI; DB  
**Requirements:** `load_interaction_policy(overrides: dict|None) -> dict` returns deterministic snapshot with:  
- `mode`, `allow_auto_vad`, `barge_in_enabled`, `auto_commit_when_ready`, and `telemetry{enabled, level, categories{…}, redaction, sampling}`  
- Shallow override semantics (replace child object).  
**Acceptance:** Snapshot keys/types match spec; overrides replace (no deep merge).

---

### B2-B — Policy Snapshot, Diffs, Hot-Reload
**Files:** `app/policy/watch.py` (new), `app/policy/loader.py` (upd), `app/voice_v2/engine.py` (upd)  
**Non-goals:** Admin UI; DB persistence  
**Requirements:**  
- `compute_diff(prev,curr) -> {added,changed,removed}` (top-level).  
- Engine `on_open` loads snapshot, emits one `policy.interaction` (incl telemetry block), publishes `EVT_POLICY_APPLIED` with diff synopsis.  
- `reapply_policy(overrides)` recomputes and re-emits only if changed.  
**Acceptance:** Initial apply emits once; unchanged reapply emits none; changed reapply emits once with correct diff.

---

### B2-C — Engine pushes `policy.interaction` (initial apply)
**Files:** `app/voice_v2/engine.py` (upd), `app/telemetry/exporter.py` (no-op)  
**Acceptance:** Exactly one `policy:applied` frame after `EVT_WS_OPEN`; exporter captures.

---

### B2-D — ACWR Recompute Breadcrumb
**File:** `app/voice_v2/engine.py` (upd)  
**Requirements:** Publish `EVT_ACWR_RECOMPUTE` with `meta={"policy_acwr": snapshot.get("auto_commit_when_ready", None), "admin_enabled": None, "effective": bool(snapshot.get("auto_commit_when_ready", True))}` on apply/reapply.  
**Acceptance:** Breadcrumb present on initial apply and any effective change.

---

### B2.5-A — WS Auth Gate + Rate Limits (Minimal)
**Files:** `app/security/auth.py` (new), `app/ws/adapter.py` (upd), `docs/10_CONTRACT_WS.md` (examples only)  
**Non-goals:** OAuth; user DB; deps  
**Acceptance:** Missing/invalid auth → unauthorized error; `EVT_AUTH_DENIED`; simple per-sid/IP token-bucket → `rate_limited` error then close on exceed.

---

### B2.5-B — Frame JSON Schema Validation (Minimal)
**Files:** `app/ws/validator.py` (new), `app/ws/adapter.py` (upd), `docs/10_CONTRACT_WS.md` (examples only)  
**Non-goals:** Full JSON Schema; vendor payloads  
**Acceptance:** Invalid frames → `{type:"error","code":"schema_invalid","detail":"..."}` (no drop).

---

### T2 — Local Tests & Runner (Build 02)
**Files:** `tests/test_policy_loader.py` (new), `tests/test_policy_apply_and_diff.py` (new), `tests/test_acwr_breadcrumb.py` (new), `scripts/run_build02_tests.sh` (new; executable)  
**Acceptance:** Runner executes the three test modules and prints `BUILD_02_TESTS: PASS` on success.
