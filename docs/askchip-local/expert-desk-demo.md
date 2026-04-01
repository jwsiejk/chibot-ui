# Expert Desk Demo — Frontstage Progress

This document is the live progress tracker for the **Expert Desk** frontstage demo flow built on AskChip Local.

## Product framing (current)

Expert Desk is the interview-facing, productized frontstage shell that keeps AskChip Local’s existing runtime contract intact while improving visual hierarchy, assistant presence, and operator confidence.

The current demo framing is:
- a dedicated **frontstage route family** for Expert Desk walkthroughs
- a **clean landing page** that frames the Expert Desk concept and entrypoint
- a **serious intake screen** that captures triage/routing metadata in structured fields
- explicit separation from the existing backstage AskChip Local shell

## Frontstage routes (implemented)

- `/demo` — Expert Desk landing screen for concept framing and handoff into intake.
- `/demo/intake` — structured intake flow for issue capture and expert routing setup.
- `/demo/recommendation` — deterministic recommendation and routing screen based on saved intake fields.

All frontstage routes run in frontend demo mode and do **not** claim backend intake, CRM, calendar, or queue-engine integration.

## Completed so far

### Foundation and Visual Session phases completed
- Local-first runtime shell and session model established.
- Canonical transcript contract preserved (`text` source of truth; shared typed + voice turn path).
- Push-to-talk voice flow implemented with explicit press lifecycle.
- WebRTC diagnostics/signaling foundation implemented without making WebRTC a hard dependency for turns.
- Streaming assistant transcript + speech alignment foundation implemented.
- Visual Session polish completed (header/stage/toolbar/drawer improvements and configurable assistant display name).

### Phase 6 — Expert Desk frontstage shell + intake
- Added frontstage demo routes: `/demo` and `/demo/intake`.
- Implemented a visually distinct frontstage shell treatment separate from backstage/dev shell UI.
- Implemented intake fields for:
  - issue category
  - environment/platform
  - urgency
  - preferred expert type
  - contact preference
  - free-text issue description
- Added optional architecture notes and error text capture areas.
- Intake persistence now uses frontend `sessionStorage` (browser session scope) through a dedicated frontstage demo-state layer; no backend handoff is implied.
- Added explicit saved-state feedback and a clear “ready for recommendation/routing” affordance that points to the Phase 7 handoff stub.

### Phase 7 — recommendation + route decision
- Implemented a real deterministic recommendation engine driven by saved intake fields (`issueCategory`, `environmentPlatform`, `urgency`, `preferredExpertType`, `contactPreference`, `issueDescription`, and optional notes/error text).
- Recommendation output now includes:
  - short issue summary
  - recommended expert type
  - recommended path:
    - continue with AI now
    - launch live expert session now
    - request follow-up session
    - escalate to human expert
- Added recommendation context cards for:
  - issue type
  - environment
  - urgency
  - expert persona
  - why the path was selected
- Added a real launch CTA that creates a local AskChip session via the existing API and routes directly into `/visual-session/:sessionId`.
- Added optional follow-up preference capture on the recommendation screen with explicit copy that it is **request capture only** in local view state and is **not persisted** to backend/calendar/queue systems.

## Remaining planned phases

> This sequence is intentionally high-level and may be updated as implementation proceeds.

1. **Guided flow scaffolds**
   - add structured panels/cards for routed tasks while preserving transcript authority
2. **Demo narrative instrumentation**
   - add lightweight event and progress indicators for live walkthroughs
3. **Stabilization + handoff packaging**
   - polish copy/motion/accessibility and freeze a repeatable demo path

## Progress log

- **2026-04-01 — Phase 5 visual polish + doc foundation**
  - polished Visual Session header/stage/toolbar/drawer behavior
  - added assistant display name configuration in runtime config
  - created this Expert Desk progress document as the live phase tracker
- **2026-04-01 — Phase 6 frontstage shell + intake routes**
  - shipped `/demo` landing and `/demo/intake` structured intake flow
  - kept backstage shell routes and behavior intact
  - documented frontend-only state persistence and no-fake-integration guardrails
- **2026-04-01 — Phase 6.1 intake durability hardening**
  - moved intake draft state into a small frontstage demo-state hook
  - persisted canonical intake draft payload via `sessionStorage` (refresh-safe and cross-frontstage-route within same browser session)
  - added saved-state readiness messaging and recommendation handoff affordance
  - added `/demo/recommendation` as an honest Phase 7 placeholder route (no routing logic yet)
- **2026-04-01 — Phase 7 recommendation/routing implementation**
  - replaced recommendation placeholder with deterministic rule-based routing derived from actual intake draft fields
  - added context-card layout and recommendation rationale panel
  - added live session launch action that creates a real local session and opens Visual Session
  - kept scheduling preference capture explicitly non-integrated and demo-only

---

## Notes and guardrails

- Backstage/dev shell behavior must remain intact.
- Transcript/state/WebRTC contract remains unchanged and authoritative.
- Intake data is persisted only in frontend `sessionStorage` for this demo phase (same browser session, refresh-safe, no backend persistence).
- Recommendation routing is deterministic and frontend-local; it is not a machine-learned policy engine.
- Follow-up “scheduling preference” capture on `/demo/recommendation` is not persisted and does not integrate with real calendar/queue services.
