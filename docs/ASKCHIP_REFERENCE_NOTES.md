# AskChip / Expert Desk Reference Notes (Retired Implementation)

## What existed before removal
The retired direction centered on AskChip Local, Expert Desk workflows, and VMware-oriented triage flows. It explored support-style intake, guided recommendation routes, and demo-oriented user journeys.

## Valuable reference patterns from prior work
- Local-first runtime exploration for private/offline-friendly operation.
- Typed chat plus push-to-talk voice interaction.
- Kokoro/local TTS experimentation.
- Canonical transcript lessons for unifying session turns.
- Visual-session framing concepts.
- Session metadata/persona/mode pre-briefing patterns.
- Streaming assistant response and typed turn flow concepts.
- Recap/summary generation patterns.

## Why old code was removed
The old repository state implied an active product direction that no longer matches AskChappy goals. Keeping runnable legacy code risked confusion, maintenance drag, and accidental continuation of support-triage UX patterns that are out of scope for AskChappy.

## Patterns that may be reused conceptually
- Unified transcript contract across typed and voice modalities.
- Simple session-state progression (`ready`, `listening`, `transcribing`, `thinking`, `speaking`, `error`).
- Optional local-first runtime strategy.
- Zoom-like session framing and recap output concept.

## Patterns that should not be carried forward
- AskChip Local / Expert Desk branding.
- VMware-first intake and triage state machine assumptions.
- Deterministic support routing as primary experience.
- Support-ticket handoff framing as core user journey.
- Legacy `/demo` and visual-session routes as active product surface.
- Diagnostics-heavy shell as default UX.
