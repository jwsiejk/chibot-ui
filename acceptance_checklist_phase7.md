# Phase 7 — Acceptance Checklist (LLM Provider + Persona Prompt)

1. LLM provider abstraction exists (`app/services/llm_provider.py`) with `get_provider()` and default = mock.
2. Config contains `llm_provider` (default 'mock') and `openai_model` keys.
3. `make_assistant_frames(seed_text, session_id, meta)` uses provider.generate_reply(...) including persona + teacher_move.
4. Persona source: session persona_id (default 'chip') → db.memory['personas'][persona_id].
5. API v1 endpoints pass `session_id` into `make_assistant_frames` (greet/chat/voice).
6. Server WS frames to client are `assistant_chunk` + `assistant_end` (no legacy names in the stream path).
7. Tests remain network-free; 'openai' provider returns deterministic stub (no external calls).
8. Route-linter still passes (no legacy '(legacy greet route)', no legacy orchestration symbol).
