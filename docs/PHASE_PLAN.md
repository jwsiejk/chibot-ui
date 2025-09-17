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

## Phase 3 — Completed (2025-09-14)
**Objective:** Implement **server-side barge-in** and **abortable TTS** per Operating Instructions.

**Status:** Implemented; tests added in `tests/phase3/*` to verify confirm timing (~420 ms), abort suppression of audio after cancel, and frame IDs.

**Acceptance (must pass):**
- Soft barge-in confirm (~420 ms) before commit; on **commit**, cancel the active `turn_id`.
- After cancel, **no new audio chunks** for the canceled `turn_id` may be emitted.
- TTS synthesis is **abortable** mid-stream.
- Assistant frames include `turn_id` and `correlation_user_msg_id` linking to the user turn.
- Tests cover: confirm/commit timing, suppression of audio after cancel, and TTS abort behavior.

---

**Note:** Phase ordering and content remain aligned with the locked architecture and public v1 surfaces.

## Phase 4 — Completed (2025-09-14)
**Objective:** Bridge `/api/v1/voice/chunk` to the streaming ASR manager and verify end-to-end **ASR partial/final** events over the WS bus.

**Status:** Implemented. The voice chunk endpoint now enqueues decoded audio into the streaming ASR manager (Deepgram path). Tests in `tests/phase4/*` send multiple chunks and assert at least one `user_partial` and a `user_final` are received on the bus.

**Acceptance (must pass):**
- `/api/v1/voice/chunk` decodes base64 audio and **enqueues** into the streaming ASR manager.
- **No fallbacks/mocks** wired for providers; bus contract remains explicit.
- Rate limiting updated to permit **~16 RPS** for voice chunks (64–128 ms cadence).
- WS bus receives **user_partial** and **user_final** frames after enough chunks.
