# BUILD 03 — WS Framing, Routing, and Versioning

**Alignment guard (do not omit):**
- Align with SSOT in `/docs` (`10_CONTRACT_WS.md`, `20_ARCH_BUILD_ORDER.md`, `30_ADR.md`).
- Touch only the files listed per task. Do **not** rename routes, env vars, or policy keys.
- Keep each new/changed file ≤ **500 lines**; ≤ **3 files per task**.
- Preserve the `chat.v2` contract (error frames `{type:"error",code,detail}`; ws taps in `meta.ws.{dir,size,preview}`).

---

### B3-A — JSON Frame Contract (Spec Parity)
**Files:** `docs/10_CONTRACT_WS.md` (examples), `app/ws/adapter.py` (upd)  
**Acceptance:** Valid `ping` returns `pong`; unknown type → `{code:"unknown_type"}`; taps include `meta.ws.*`; 64KB text guard → 1009.

---

### B3-B — Binary Routing Guard
**Files:** `app/ws/adapter.py` (upd)  
**Acceptance:** Binary accepted only when engine expects audio; else `{code:"audio_not_expected"}`; after repeated violations, 1008 close.

---

### B3-C — Version Negotiation & Backpressure Events
**Files:** `app/ws/adapter.py` (upd), `docs/10_CONTRACT_WS.md` (examples), `app/voice_v2/engine.py` (comment hint)  
**Acceptance:** Bad subprotocol → HTTP 426 with `{code:"bad_subprotocol",detail:"use chat.v2"}`; backpressure emits `EVT_BACKPRESSURE_ON/OFF` with queue depth.

---

### T3 — Local Tests & Runner (Build 03)
**Files:** `tests/test_ws_json_contract.py` (new), `tests/test_ws_binary_guard.py` (new), `scripts/run_build03_tests.sh` (new; executable)  
**Acceptance:** Runner executes both tests and prints `BUILD_03_TESTS: PASS` on success.
