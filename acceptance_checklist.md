# Phase 6 — Acceptance Checklist

Front-end Presence & Behavior (v1-only, single WS per tab)

1. State machine exists with explicit states: ready → listening → thinking → responding → ready.
2. Start opens WS /ws/v1/chat, then calls GET /api/v1/greet (in that order). Start disabled while WS open; End closes WS.
3. One WebSocket per tab is enforced; reconnect attempts ≤1.
4. Listening arms VAD; Responding boosts VAD threshold; soft barge-in: pause on first VAD hit, confirm ~420 ms, then interrupt.
5. Nudge fires ~4.2 s after assistant_end if user silent; cancels on any user action.
6. Suggestion chips render under the last assistant message, ≤4 chips, each ≤7 words, click-to-send.
7. Text composer posts to /api/v1/chat; shows 'thinking' until first assistant token/voice.
8. Error banner shows exact failing route + HTTP status; no hidden fallbacks.
9. Route linter: fails if any legacy routes are present (e.g., '/api/greet', '/api/chat' without '/v1/', 'orchestrator').
10. Design Mode (Atomic) remains inert when off; does not alter normal layout flow.

\1Verified: 2025-09-17 via Data Analysis tests

## Phase 5 — WS TTS Streamer
Verified: 2025-09-17 via Data Analysis tests
