# RULES

Operate strictly per the locked architecture and surfaces defined in **docs/OPERATING_INSTRUCTIONS.md** and the phased acceptance in **docs/PHASE_PLAN.md**.

- **No fallbacks.** Errors must be explicit and actionable.
- **No mocks.** Real vendors only (ASR, TTS, LLM, SMTP). If a key is missing, fail fast.
- **Production-optimal changes** only; remove dead code/config; explicit failures; legacy routes 404/410 with tests.
- **Full files only** in patches; tests accompany every change.

## Phase Status
- ✅ **Phase 0** — v1-only surfaces, greet idempotent, `/api/v1/voice/chunk`, providers fail-fast.
- ✅ **Phase 1** — (Completed previously per project thread; details tracked in repo history/tests.)
- ✅ **Phase 2** — **Login/Profile gate with Neon:** on login, check Neon for existing profile; if present, skip profile modal and enable Start; if absent, present profile, allow save to Neon, then enter main interface. (Completed 2025-09-13.)
- ✅ **Phase 3** — Server-side barge-in and abortable TTS (Completed) — Server-side **barge-in** (soft confirm ~420ms then commit) and **abortable TTS**; commit cancels active `turn_id` and suppresses further audio frames.

See **docs/PHASE_PLAN.md** for acceptance items and **docs/OPERATING_INSTRUCTIONS.md** for operating details.
