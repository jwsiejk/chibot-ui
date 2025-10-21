# BUILD 02 — Policy Manager & Application (with Telemetry Block)

**Alignment guard (do not omit):**
- Align with SSOT in `/docs` (`00_CONTEXT.md`, `10_CONTRACT_WS.md`, `15_NLU_NLG.md`, `20_ARCH_BUILD_ORDER.md`, `30_ADR.md`).
- Touch only listed files; ≤ 500 LOC/file; ≤ 3 files/task.
- Preserve `chat.v2`; all policy frames must include the policy telemetry block.

---

### B2-A: Policy Defaults incl. Telemetry
**Files:** `app/policy/loader.py` (new), `docs/10_CONTRACT_WS.md` (update examples)  
**Non-goals:** Engine wiring beyond snapshot return; ASR/TTS logic  
**Acceptance:**  
- Loader returns snapshot with: `mode`, `allow_auto_vad`, `barge_in_enabled`, `auto_commit_when_ready`, `telemetry` (enabled, level, categories, redaction, sampling).  
- Deterministic defaults; typed; unit smoke returns stable keys.

---

### B2-B: Policy Snapshot, Diffs, Hot-Reload
**Files:** `app/policy/loader.py` (update), `app/policy/watch.py` (new), `app/voice_v2/engine.py` (update)  
**Non-goals:** Admin UI; DB-backed policies  
**Acceptance:**  
- On open, engine emits `policy:applied` frame with `snapshot` and `diff:{added,changed,removed}` (empty `diff` on first apply).  
- Hot-reload via env/file watch toggles re-apply; precedence: admin override > env > defaults (documented).  
- Telemetry: `EVT_POLICY_APPLIED` with diff synopsis.

---

### B2-C: Engine pushes `policy.interaction` (initial apply)
**Files:** `app/voice_v2/engine.py` (update), `app/telemetry/exporter.py` (no functional change)  
**Non-goals:** Gates/ASR/TTS behavior changes  
**Acceptance:**  
- Exactly one `policy:applied` after `EVT_WS_OPEN`, includes telemetry block and core policy keys; captured to exporter.

---

### B2-D: ACWR Recompute Breadcrumb
**Files:** `app/voice_v2/engine.py` (update)  
**Non-goals:** Changing final ACWR behavior  
**Acceptance:**  
- Emits breadcrumb `EVT_ACWR_RECOMPUTE` with `{policy_acwr, admin_enabled, effective}` on each apply or override.

---

### B2.5-A: WS Auth Gate + Rate Limits
**Files:** `app/security/auth.py` (new), `app/ws/adapter.py` (update), `docs/10_CONTRACT_WS.md` (update)  
**Non-goals:** OAuth flows; DB users; UI  
**Acceptance:**  
- Adapter validates bearer or signed cookie (pluggable in `auth.py` stub); unauth → 4401 JSON error frame + close.  
- Simple per-IP and per-sid token bucket (config constants) → on exceed, error `rate_limited` + close; publish `EVT_RATE_LIMIT`.  
- Telemetry `EVT_AUTH_DENIED` on auth failure. Typed; ≤ 500 LOC/file.

---

### B2.5-B: Frame JSON Schema Validation
**Files:** `app/ws/validator.py` (new), `app/ws/adapter.py` (update), `docs/10_CONTRACT_WS.md` (update schemas)  
**Non-goals:** Vendor-specific payload schemas  
**Acceptance:**  
- Validate text frames against minimal schemas; invalid → `{"type":"error","code":"schema_invalid","hint":"<field> missing"}` (no drop unless spec says).  
- Unit smoke covering valid/invalid; adapter calls validator before forwarding.

> “Return only diffs for the files listed above. Do not modify or create any other files.”
