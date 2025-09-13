# PHASE_PLAN

This document tracks acceptance items per phase and serves as the canonical checklist. See **RULES.md** for guardrails and **docs/OPERATING_INSTRUCTIONS.md** for the locked architecture.

## Phase Progress
- [x] **Phase 0** — v1-only HTTP/WS, greet idempotent, `/api/v1/voice/chunk`, explicit provider selection (no mocks), legacy routes 404/410, tests in place.
- [x] **Phase 1** — Completed (per prior session).  
- [x] **Phase 2** — **Neon-backed Login/Profile Gate** (Completed 2025-09-13)
  - `/api/v1/auth/login` loads profile from Neon (when `DATABASE_URL` is set).
  - If profile exists → **no profile modal**, **Start enabled**.
  - If profile absent → **profile modal** shown; `/api/v1/profile` persists to Neon; after save → **enter main interface** and **Start enabled**.
  - CSRF honored on all state-changing calls.
  - Tests: `tests/phase2/test_auth_profile_neon_phase2.py` validate both present/absent paths using a SQLite DSN (compatible with Neon path).

## Phase 3 — Next
**Objective:** Implement **server-side barge-in** and **abortable TTS** per Operating Instructions.

**Acceptance (must pass):**
- Soft barge-in confirm (~420 ms) before commit; on **commit**, cancel the active `turn_id`.
- After cancel, **no new audio chunks** for the canceled `turn_id` may be emitted.
- TTS synthesis is **abortable** mid-stream.
- Assistant frames include `turn_id` and `correlation_user_msg_id` linking to the user turn.
- Tests cover: confirm/commit timing, suppression of audio after cancel, and TTS abort behavior.

---

**Note:** Phase ordering and content remain aligned with the locked architecture and public v1 surfaces.
