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
- Mid-session mode change is allowed and must be logged as a system transcript event.
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

- Transition events must append a canonical `system` message with at least:
  - transition type (`mode_change`)
  - `from_mode`
  - `to_mode`
  - actor (`user` or `system`)
- Transition events must not overwrite prior metadata/transcript entries.
- Recap generators must read transition events in chronological order and treat the latest mode as active final mode.

### 3.5 Validation and fallback behavior

- Invalid `session_mode` input must fail fast with a typed validation error.
- Recovery behavior: if an invalid mode is encountered in a persisted payload, runtime must fall back to `open_qa` and log a `system` correction event.
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
- Legacy `/demo*` and `/visual-session*` routes are absent from active UX.
