# AskChappy Rebuild Plan

## Phase 0 — Docs-only cleanup
- Remove stale app/runtime code and outdated product framing.
- Preserve essential lessons as reference docs only.

## Phase 1 — New app skeleton
- Create fresh repository/app structure for AskChappy implementation.
- Define clean module boundaries for UI, session, transcript, and runtime adapters.
- Enforce `docs/IMPLEMENTATION_CONTRACTS.md` as required scaffold input before coding routes/runtime behavior.

## Phase 2 — Sessions and canonical transcript
- Implement transcript-first session engine.
- Enforce `text`/`role`/`source` canonical contract.

## Phase 3 — Zoom-like Chappy UI
- Build stage-first layout with Chappy-centered session experience.
- Integrate transcript/chat panel using canonical transcript source.

## Phase 4 — Open Q&A persona/runtime
- Ship default Open Q&A flow.
- Apply Chappy persona behavior layer for DDN partner enablement.

## Phase 5 — Guided modes
- Implement guided mode overlays (not separate bots).
- Ensure in-session switching between Open Q&A and guided overlays.

## Phase 6 — Voice input/output
- Add speech input and TTS output over shared transcript pipeline.
- Ensure no modality bypasses canonical transcript.

## Phase 7 — Chappy voice clone integration
- Integrate consented Chappy voice provider through TTS abstraction.
- Keep voice assets outside public repository.

## Phase 8 — Chappy avatar integration
- Progress from placeholder to richer avatar states.
- Add future-ready hooks for speaking/viseme animation.

## Phase 9 — Partner recap/summary
- Generate session summaries, action items, and talk tracks.
- Ground outputs strictly in transcript + session metadata.

## Phase 10 — Packaging/deployment
- Define deployment targets, configuration profiles, and release workflows.
- Add production hardening and operational checks.
