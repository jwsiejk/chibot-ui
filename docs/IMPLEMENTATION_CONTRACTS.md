# AskChappy V1 Implementation Contracts

This document defines the implementation-level contracts that all future AskChappy code must follow.

## 1) Route map

### Canonical V1 routes
- `/` — redirect or link to `/chappy`
- `/chappy` — AskChappy entry room / start screen
- `/chappy/session/:sessionId` — Zoom-like live Chappy session
- `/chappy/summary/:sessionId` — partner enablement recap
- `/dev` — optional local diagnostics route, hidden from main user flow

### Retired routes policy
The following legacy routes must **not** be reintroduced as active primary UX:
- `/demo`
- `/demo/intake`
- `/demo/recommendation`
- `/visual-session/:sessionId`
- `/demo/summary/:sessionId`

These retired routes may be referenced **only** in `docs/ASKCHIP_REFERENCE_NOTES.md` as historical context.

## 2) Session metadata contract

Canonical AskChappy session metadata shape:

```json
{
  "askchappy": {
    "persona_id": "ddn_chappy_vptm",
    "persona_label": "Chappy",
    "session_mode": "open_qa",
    "audience": "partner_seller_or_se",
    "topic": null,
    "desired_output": "answer_questions_and_offer_guidance",
    "context": {
      "customer_name": null,
      "partner_name": null,
      "industry": null,
      "use_case": null,
      "competitor": null,
      "meeting_goal": null
    }
  }
}
```

Contract rules:
- `askchappy` is the top-level namespace for persona/session metadata.
- `persona_id` and `persona_label` identify one active assistant identity.
- `session_mode` defaults to `open_qa`; guided modes are overlays, not separate bots.
- `context` fields are optional inputs and may remain `null` until provided.
- Summary generation must be grounded in canonical transcript + this metadata shape.

## 3) Guided mode behavior contract

This section defines the implementation-ready behavior contract for guided modes in V1.

### 3.1 Mode enum

Allowed V1 `session_mode` values:

```text
open_qa
learn_ddn
meeting_prep
pitch_practice
objection_handling
competitive_positioning
technical_deep_dive
follow_up_builder
```

Rules:
- `session_mode` is required in runtime state and persisted metadata for each session.
- Unknown mode values must be rejected at validation boundaries (client guard + server/API guard when present).
- If no explicit mode is chosen, runtime must initialize as `open_qa`.

### 3.2 Cross-mode invariants

Guided modes are behavioral overlays and must follow these invariants:
- One assistant identity: guided modes do not change `persona_id` or `persona_label`.
- One transcript: all mode interactions persist into the same canonical transcript schema.
- One route pattern: guided mode sessions still use `/chappy/session/:sessionId`.
- Mid-session mode change is allowed and must be auditable as a session event or metadata event.
- Mode changes must not automatically create visible chat/transcript messages.
- Chappy may acknowledge a mode change in a normal assistant message when useful; that acknowledgement is then part of the canonical transcript because Chappy actually said/displayed it.
- Internal app-state events must not be represented as fake user or assistant messages.
- Recaps must include the active mode at generation time and may summarize mode transitions.

### 3.3 Mode behavior table (implementation contract)

| `session_mode` | Primary user intent | Assistant default behavior | Suggested kickoff prompt (system/runtime seed) | Recap emphasis |
|---|---|---|---|---|
| `open_qa` | General product/partner Q&A | Answer directly, clarify where needed, offer optional next steps | “Ask me anything about DDN positioning, use cases, or partner scenarios.” | Key answers and follow-up opportunities |
| `learn_ddn` | Build foundational DDN understanding | Teach in progressive layers, check understanding, define terminology | “Let’s build your DDN knowledge from basics to practical field usage.” | Concepts learned, terminology, confidence gaps |
| `meeting_prep` | Prepare for a customer/partner meeting | Gather meeting context, generate agenda, objectives, and talk tracks | “Let’s prepare your meeting plan, message, and likely decision criteria.” | Agenda, talk track, discovery questions, risks |
| `pitch_practice` | Rehearse delivery and narrative | Run roleplay, score clarity/value alignment, provide coaching revisions | “Deliver your pitch and I’ll coach structure, clarity, and impact.” | Strengths, improvement points, revised pitch draft |
| `objection_handling` | Handle pushback and concerns | Simulate objections, provide response frameworks, refine counters | “Share objections you expect and we’ll build concise response plays.” | Objection-response pairs and escalation points |
| `competitive_positioning` | Position against alternatives | Compare by use case and outcomes, avoid unsupported claims | “Let’s map DDN differentiation for your target scenario and competitor set.” | Differentiators, proof points, safe claim boundaries |
| `technical_deep_dive` | Explore architecture and implementation details | Go deep technically, state assumptions, separate fact vs hypothesis | “We’ll go deep on architecture, integration, and operational considerations.” | Architecture notes, dependencies, open technical questions |
| `follow_up_builder` | Draft post-meeting follow-up assets | Turn transcript/context into actionable written follow-up content | “Let’s build your follow-up email, action items, and next-step messaging.” | Draft follow-up artifacts and owner-tagged actions |

### 3.4 Mode transitions and lifecycle

- Mode transitions preserve `session_id`.
- Mode transitions preserve transcript history.
- Mode transitions update `metadata.askchappy.session_mode` and, when applicable, `metadata.askchappy.context`.
- Mode transitions must be recorded in an auditable session-event stream or metadata-event list.
- A mode transition event should include at least:
  - `event_type`: `mode_change`
  - `from_mode`
  - `to_mode`
  - `actor`: `user`, `assistant`, or `system`
  - `created_at`
- Mode transition events are not chat messages by default.
- Mode transition events must not be rendered in the visible chat transcript unless the UI deliberately has a separate diagnostics/events view.
- Chappy may optionally acknowledge the switch in a normal assistant message, for example: “Got it — I’ll switch into meeting prep mode.”
- If Chappy acknowledges the switch, that acknowledgement is a normal assistant transcript message with `role=assistant` and `source=assistant_stream` or equivalent.
- Recap generators may read mode transition events in chronological order and treat the latest valid mode as active final mode.
- Recap generators should not pretend internal mode events were spoken by the user or Chappy.

### 3.5 Transcript-visible messages vs session events

- The canonical transcript is for things the user typed, the user said, Chappy displayed, Chappy said, or explicit system messages intended to be visible.
- Internal state changes, routing decisions, UI events, and diagnostics belong in session events or metadata events.
- Do not create fake transcript messages to represent internal state.
- `system` transcript messages are allowed only for user-visible or recap-relevant system notices, not routine hidden app events.
- Session events may be used for audit, diagnostics, replay, and recap context.
- Chat and transcript remain interchangeable views of conversational content, not raw application telemetry.

### 3.6 Validation and fallback behavior

- Invalid `session_mode` input must fail fast with a typed validation error.
- Recovery behavior: if an invalid mode is encountered in a persisted payload, runtime must fall back to `open_qa`.
- The correction must be recorded as a session event or metadata event.
- A visible `system` transcript message should only be added if the user needs to see the correction.
- Fallback must preserve all existing transcript records unchanged.

## 4) Canonical transcript schema contract

All modalities (typed, voice input, assistant stream/final, recap) must map to one canonical transcript model.

Required message shape:

```json
{
  "id": "msg_...",
  "ts": "2026-05-23T00:00:00.000Z",
  "role": "user",
  "text": "message text",
  "source": "typed",
  "session_id": "session_...",
  "meta": {}
}
```

Rules:
- `text` is canonical (never replace with `content`).
- `role` allowed values: `user`, `assistant`, `system`.
- `source` allowed values (V1): `typed`, `voice`, `assistant_stream`, `speech`, `system`, `summary`.
- Voice output must be derived from the exact assistant `text` committed to transcript.
- No modality may bypass transcript persistence.
- Internal app events must not be forced into the canonical transcript.
- Use session events / metadata events for internal mode changes, diagnostics, and lifecycle telemetry.
- If an event is intended to be visible in chat, it must be represented as a deliberate `system` message; otherwise it remains outside the visible transcript.

## 5) Session state machine contract

Minimum V1 states:
- `ready`
- `listening`
- `transcribing`
- `thinking`
- `speaking`
- `error`

Rules:
- UI and runtime must expose coherent current state.
- State transitions must be serializable/replayable for diagnostics.
- `error` state must preserve latest transcript and allow recovery/retry path.

## 6) Summary and recap contract

- Recaps are partner enablement artifacts, not support-case handoff tickets.
- Summary outputs must be generated from canonical transcript + session metadata.
- V1 summary types may include: notes, action items, talk tracks, and follow-up content.
- Summary route contract: `/chappy/summary/:sessionId` resolves by `sessionId` tied to canonical transcript.

## 7) Non-goals and guardrails for implementation PRs

- Do not reintroduce AskChip/Expert Desk/VMware retired runtime UX as primary flows.
- Do not add separate bot identities for guided modes.
- Do not introduce modality-specific parallel chat stores.
- Do not store private voice clone training assets in the public repository.

## 8) Compliance checklist for first implementation PRs

- Route scaffold matches canonical V1 route map.
- Session metadata object conforms to this contract.
- Transcript entries enforce `text`/`role`/`source` semantics.
- Session states include `ready`→`error` minimum set.
- Summary generation pipeline is transcript + metadata grounded.
- Mode changes are auditable without automatically creating visible chat messages.
- Internal session events are separated from conversational transcript messages.
- No fake user/assistant transcript messages are created for app-state changes.
- Recap generation may use session events, but must distinguish them from spoken/displayed conversation.
- Legacy `/demo*` and `/visual-session*` routes are absent from active UX.

## 9) MVP auth, role, and admin route contract

### 9.1 Login model (local-first only)
- `/chappy` entry includes an email-only login modal for local MVP.
- No password is required in local MVP mode.
- Email is used for local-first role selection and personalization only.
- This is email-only local auth for the current local production MVP; future enterprise auth may replace it.

### 9.2 Role model
Allowed V1 MVP roles:

```text
standard_user
admin
```

Initial admin rule:
- `jsiejk@ddn.com` => `admin`
- Any other email => `standard_user`

Permissions contract:
- `standard_user` can enter AskChappy, run Open Q&A/guided sessions, view recap, and hear the published Chappy voice.
- `standard_user` cannot access Voice Studio or publish/modify Chappy voice profiles.
- `admin` can access admin controls, run Voice Studio workflow, and approve/publish/disable active voice profiles when implemented.

Important framing:
- Admin-only controls exist for UX control and shared voice consistency, not heavy security hardening for MVP.

### 9.3 Admin route map additions
Planned admin routes:
- `/admin` — admin dashboard
- `/admin/voice` — Voice Studio
- `/admin/avatar` — future avatar setup/review

Access behavior:
- Admin routes are hidden from standard user navigation.
- Standard-user direct access to admin routes must return a simple “not authorized” state.
- Voice cloning controls must never appear in normal `/chappy/session/:sessionId` user sessions.

## 10) Project structure and anti-bloat compliance

- Implementation PRs must comply with `docs/PROJECT_STRUCTURE_AND_CODE_GUARDRAILS.md`.
- Shared code contracts belong in `shared/contracts` and must not be duplicated across UI/API code.
- UI and API modules should follow documented domain folder boundaries to avoid tangled architecture.
- Route changes must remain aligned with canonical route contracts and stale-route retirement policy.
