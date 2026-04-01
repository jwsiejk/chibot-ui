# Expert Desk Demo — Frontstage Progress

This document is the live progress tracker for the **Expert Desk** frontstage demo flow built on AskChip Local.

## Product framing (current)

Expert Desk is the interview-facing, productized frontstage shell that keeps AskChip Local’s existing runtime contract intact while improving visual hierarchy, assistant presence, and operator confidence.

The current demo framing is:
- a **single visual session stage** where assistant state is obvious at a glance
- a **shared canonical transcript** for typed and push-to-talk turns
- a **clean interaction drawer** for active messaging and voice controls
- a **future-ready frontstage surface** for later intake/routing experiences (not implemented yet)

## Completed so far (through current Visual Session work)

### Foundation phases completed
- Local-first runtime shell and session model established.
- Canonical transcript contract preserved (`text` source of truth; shared typed + voice turn path).
- Push-to-talk voice flow implemented with explicit press lifecycle.
- WebRTC diagnostics/signaling foundation implemented without making WebRTC a hard dependency for turns.
- Streaming assistant transcript + speech alignment foundation implemented.

### Visual Session polish completed in this phase
- Strengthened visual hierarchy in the session header and stage shell.
- Added configurable assistant display name via frontend runtime config (`VITE_ASKCHIP_ASSISTANT_DISPLAY_NAME`).
- Reworked stage presentation to feel intentional (assistant frame + lower-third/nameplate treatment).
- Preserved existing state-driven glow/badge behavior for `ready/listening/transcribing/thinking/speaking/error`.
- Improved toolbar clarity and active states (chat-open and voice-active cues).
- Improved drawer behavior and closure ergonomics, including Escape-to-close behavior.
- Improved loading and empty-state copy to feel user-facing rather than placeholder/prototype.

## Remaining planned phases

> This sequence is intentionally high-level and may be updated as implementation proceeds.

1. **Frontstage context strip + session framing hardening**
   - tighten title/context metadata and demo storytelling affordances
2. **Expert Desk intake and routing surfaces** *(not started)*
   - introduce first-run intake and route-to-flow decisions
3. **Guided flow scaffolds**
   - add structured panels/cards for routed tasks while preserving transcript authority
4. **Demo narrative instrumentation**
   - add lightweight event and progress indicators for live walkthroughs
5. **Stabilization + handoff packaging**
   - polish copy/motion/accessibility and freeze a repeatable demo path

## Progress log

- **2026-04-01 — Phase 5 visual polish + doc foundation**
  - polished Visual Session header/stage/toolbar/drawer behavior
  - added assistant display name configuration in runtime config
  - created this Expert Desk progress document as the live phase tracker

---

## Notes and guardrails

- Intake/routing screens are intentionally deferred to a later phase.
- Backstage/dev shell behavior must remain intact.
- Transcript/state/WebRTC contract remains unchanged and authoritative.
