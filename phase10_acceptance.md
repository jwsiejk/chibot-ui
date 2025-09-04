# Phase 10 — Platform vs Persona
- Personas are **config-only** packs (no code) with a strict validator.
- Admin Persona API (gated): CRUD, import/export, preview, publish/rollback.
- Sessions carry `persona_id`; preview ties a persona to a live session.
- All persona actions are **audited** and broadcast on admin events.
- v1-only; WS-only chat path retained; vendors mocked.
