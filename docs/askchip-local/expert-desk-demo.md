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
- `/demo/summary/:sessionId` — post-session handoff summary resolved by session id.

All frontstage routes run in frontend demo mode and do **not** claim backend intake, CRM, calendar, or queue-engine integration.

### Frontstage flow (final consolidated demo)
1. `/demo` frames the AI Expert Desk story and routes into intake.
2. `/demo/intake` captures structured routing context and saves it in frontend-local `sessionStorage`.
3. `/demo/recommendation` computes deterministic specialist routing and launches a real local AskChip session id.
4. `/visual-session/:sessionId` runs the live expert session with assistant-stage-first focus and Expert Desk context assist rails.
5. `/demo/summary/:sessionId` provides summary, handoff request capture, and next-step framing tied to that same session id.

A lightweight progress treatment now appears across frontstage stages to keep walkthrough narrative coherence.

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
- Added explicit saved-state feedback and a clear “ready for recommendation/routing” affordance that points to the Phase 7 handoff placeholder.

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
- Recommendation now includes explicit handoff-model copy describing real session-id creation plus frontend-local context carryover only.

### Phase 8 — session-linked frontstage context handoff
- Added a dedicated frontend-local session context utility keyed by visual-session id (`sessionStorage`) to carry frontstage Expert Desk context across redirect.
- Recommendation launch now performs a deterministic handoff flow:
  1. create a real AskChip local session
  2. build Expert Desk frontstage context from saved intake + deterministic recommendation output
  3. store that context under the created session id
  4. redirect to `/visual-session/:sessionId` where the context is read back by session id
- Visual Session now renders a compact Expert Desk context strip when handoff data is present, including:
  - customer/request label
  - issue category
  - environment
  - urgency
  - expert persona
  - recommended path
- Visual Session now includes an **Expert Assist** rail that surfaces:
  - recommended next step
  - likely topic/root-cause hint
  - retrieved case context explicitly labeled as sourced from saved intake/recommendation data in this browser session
  - escalation note
- Intake “ready for recommendation” logic now requires **current valid intake fields + saved state**, so stale ready status no longer persists after fields become invalid.

### Phase 9 — post-session summary / handoff closeout
- Added a dedicated frontstage summary route: `/demo/summary/:sessionId`.
- Added a deliberate transition from live visual session to summary via **End session and view summary** (no automatic redirect).
- Summary data assembly now combines:
  - actual session + transcript data loaded from the real local API (`GET /api/v1/sessions/:sessionId/transcript`)
  - optional session-linked Expert Desk context loaded by session id from frontend `sessionStorage`
- Summary now presents:
  - issue summary
  - key captured context
  - actions taken explicitly labeled as **derived from transcript/session data**
  - recommended next steps and escalation note
  - transcript follow-up affordance back to the same `/visual-session/:sessionId`
  - optional local-only next-step request capture (follow-up session or human escalation) saved in frontend session storage only
- Missing-data handling:
  - if transcript/session loads but no Expert Desk context exists, summary falls back to honest session-derived framing
  - if session id is invalid/deleted, summary shows a user-facing unavailable message with recovery links

### Phase 10 — final coherence and walkthrough consolidation
- Standardized frontstage copy to consistently use AI Expert Desk, expert routing/specialist engagement, live session, and summary/handoff language.
- Added a minimal shared progress indicator across landing, intake, recommendation, live session, and summary to improve walkthrough clarity.
- Resolved live-session wrap-up wording mismatch:
  - CTA now reads **View summary and handoff**
  - UI explicitly states this is walkthrough navigation and does not perform backend session termination.
- Finalized summary handoff-request behavior:
  - latest saved request (type, timestamp, and note) is now rendered clearly
  - request is explicitly labeled frontend-local only and non-integrated.
- Tightened edge-case and transition affordances:
  - clearer fallback state when visual session is opened without Expert Desk context
  - explicit return links between recommendation/live-session and summary/live-session steps.

### Phase 11 — backend session metadata handoff foundation
- Added optional typed session-create metadata handoff for Expert Desk context (`metadata.expert_desk`) from the recommendation launch path.
- Backend session creation now accepts and persists that optional Expert Desk metadata on the real session record, while preserving title-only session creation compatibility.
- This phase intentionally does **not** change transcript message shape, turn-submit contract, or runtime prompting behavior; it only establishes pre-turn, session-scoped context persistence for future runtime pre-briefing.

## Demo Walkthrough (recommended)
1. Open `/demo` and frame this as the **frontstage AI Expert Desk** experience, distinct from backstage AskChip shell.
2. Select **Start intake** and complete `/demo/intake` required fields; click **Save intake draft**.
3. Use **Continue to recommendation handoff** and review deterministic routing on `/demo/recommendation`.
4. Click **Launch live expert session** to create a real local session id and carry frontend-local context into live session.
5. In `/visual-session/:sessionId`, show assistant-stage behavior first, then show Expert Assist context rail.
6. Click **View summary and handoff** to move into `/demo/summary/:sessionId`.
7. In summary, review:
   - session/transcript-derived actions
   - recommended next steps
   - optional local handoff request capture and the displayed latest saved local request.
8. Optionally reopen the same transcript via **Open live session transcript** for continuity.


## Remaining planned phases

> This sequence is intentionally high-level and may be updated as implementation proceeds.

1. **Roadmap hygiene only**
   - optional micro-polish (animation pacing/accessibility copy tightening)
2. **Backend integration planning (future)**
   - only if/when product scope expands beyond frontend-local demo storage and deterministic routing

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
- **2026-04-01 — Phase 8 session-linked frontstage handoff + visual expert assist**
  - added deterministic sessionStorage-backed context binding from recommendation launch to visual-session id
  - rendered Expert Desk context strip and Expert Assist rail in Visual Session while keeping assistant stage as the focal point
  - fixed readiness gating so recommendation readiness reflects current valid intake fields instead of historical save alone
  - kept runtime/transcript contract intact with no backend integration claims
- **2026-04-02 — Phase 9 summary / handoff closeout route**
  - added `/demo/summary/:sessionId` with session-id-based transcript/context resolution
  - added deliberate wrap-up CTA in live visual session: **End session and view summary**
  - assembled summary using real session/transcript data plus optional session-linked Expert Desk context
  - added local-only follow-up/escalation request capture persisted to frontend session storage (explicitly non-integrated)
  - added graceful failure handling for missing/deleted sessions and context gaps
- **2026-04-02 — Phase 10 final frontstage consolidation**
  - unified frontstage flow narrative and copy from landing through summary
  - added shared progress indicator across all frontstage flow stages
  - replaced ambiguous wrap-up CTA with **View summary and handoff** plus explicit no-backend-termination wording
  - rendered latest saved local handoff request note clearly in summary with frontend-local-only labeling
  - finalized walkthrough guidance for repeatable demo delivery
- **2026-04-02 — Recommendation view naming cleanup**
  - renamed the implemented recommendation screen component/file from `ExpertDeskRecommendationStubView` to `ExpertDeskRecommendationView`
  - updated app route import/usage references to match and removed stale "stub" identifier wording
  - no runtime, routing, UI, transcript, or session contract behavior changes
- **2026-04-02 — Phase 11 backend metadata handoff plumbing**
  - extended recommendation launch to send optional typed `metadata.expert_desk` on `POST /api/v1/sessions`
  - extended backend session-create API model/path to accept and persist optional Expert Desk session metadata
  - preserved backward compatibility for existing title-only session creation and backstage shell behavior
  - kept transcript contract and turn-submit contract unchanged (no prompt/runtime behavior changes yet)

---

## Notes and guardrails

- Backstage/dev shell behavior must remain intact.
- Transcript/state/WebRTC contract remains unchanged and authoritative.
- Intake data is persisted only in frontend `sessionStorage` for this demo phase (same browser session, refresh-safe, no backend persistence).
- Recommendation routing is deterministic and frontend-local; it is not a machine-learned policy engine.
- `/demo/recommendation` provides routing rationale and handoff-model copy only; no calendar/queue request capture is performed there.
- Session-linked frontstage context for Visual Session is still frontend-local (`sessionStorage`) and browser-session scoped for current demo UI rendering.
- Recommendation launch now also supports an optional backend session-scoped metadata handoff (`metadata.expert_desk`) persisted on the AskChip session record for future runtime pre-briefing.
- Summary handoff requests (follow-up/escalation) are frontend-local (`sessionStorage`) by session id and are not sent to backend CRM, scheduling, or ticketing systems.
