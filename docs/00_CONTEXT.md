# AskChip v2 — Canonical Context

AskChip is a **voice-driven, real-time conversational platform** that hosts virtual AI employees (vPTMs). The first vPTM is **Chip**. Users speak; Chip listens, thinks, and speaks back, with full auditability.

## v2 Scope (locked)
- **Single WS endpoint:** `/ws/v2/chat` (subprotocol **`chat.v2`**). No v1 path.
- **ASR:** Deepgram (primary), Speechmatics (secondary). *Whisper is not used.*
- **Barge-in:** **Automatic only** (no PTT). Policy toggle: `barge_in_enabled`.
- **UI:** waveform + state badges (Ready / Listening / Thinking / Responding). No avatar/visemes yet.
- **Policy frames ALWAYS include:** `mode`, `allow_auto_vad`, `barge_in_enabled`, `auto_commit_when_ready`, and **`telemetry`** block.
- **ACWR precedence:** `effective = policy_state AND admin_switch`. (No runtime cfg input.)
- **Templates root:** `app/templates/` only.

## Lifecycle (one turn)
1) Server sends `tts.start` → audio plays → `tts.end`.
2) Post-hold delay → policy switches to `idle` with `auto_commit_when_ready:true`.
3) User speech → ASR partials/final → NLU → Dialog Policy → NLG → TTS.
4) Telemetry logs **every** step, on both client and server, under a single schema.

## Future-proofing (vPTM roles, presentations, workflows)
- Personas are **config packs** (import/export later). Engine reads persona config; no code changes needed.
- Telemetry Bus fans out events; future **Workflow**/**Presentation** managers subscribe without touching the core.
- DB has room to add `personas`/`workflows` tables later; current code doesn’t require them.

## Non-goals (v2)
- No manual PTT.
- No avatar/visemes.
- No tool/workflow execution (only seams/hooks exist).
AskChip v2 canonical context.