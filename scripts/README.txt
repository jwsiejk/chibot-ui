# Phase 9 Hotfix Bundle

This applies the exact final-sweep changes:
- HTTP 426 Upgrade hint on `/ws/v1/chat`
- Remove SSE on WS path
- Rate-limit exemptions for `nudge`, `interrupt`, `end_session`
- Greet/STT broadcast initial frames to the WS bus
- Mirror transcript emails to `db.list_emails()`

## How to use
1) Copy `scripts/apply_phase9_hotfix.py` into the **backend repo root** (the one with `app/`).
2) From that repo root:
   ```bash
   python scripts/apply_phase9_hotfix.py
   git add -A
   git status
   ```
3) You should see modified files. Review and commit.

If you still see no changes, confirm you’re in the backend repo (look for `app/asgi_gateway.py`).

## Verify quickly
- `GET /ws/v1/chat` → HTTP 426 with `Upgrade: websocket`
- `/api/v1/greet?session_id=...` → enqueues `state`, `suggestions`, `assistant_chunk`, `assistant_end` on the WS bus
- `/api/v1/voice/stt` (multipart) → enqueues a `text` and `audio_chunk`
- `nudge` twice → `{nudged:true}`, third `{nudged:false}`
- `end_session` → mirrors to `db.list_emails()` (and enqueues to outbox)
