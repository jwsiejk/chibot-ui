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

## 3) Canonical transcript schema contract

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

## 4) Session state machine contract

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

## 5) Summary and recap contract

- Recaps are partner enablement artifacts, not support-case handoff tickets.
- Summary outputs must be generated from canonical transcript + session metadata.
- V1 summary types may include: notes, action items, talk tracks, and follow-up content.
- Summary route contract: `/chappy/summary/:sessionId` resolves by `sessionId` tied to canonical transcript.

## 6) Non-goals and guardrails for implementation PRs

- Do not reintroduce AskChip/Expert Desk/VMware retired runtime UX as primary flows.
- Do not add separate bot identities for guided modes.
- Do not introduce modality-specific parallel chat stores.
- Do not store private voice clone training assets in the public repository.

## 7) Compliance checklist for first implementation PRs

- Route scaffold matches canonical V1 route map.
- Session metadata object conforms to this contract.
- Transcript entries enforce `text`/`role`/`source` semantics.
- Session states include `ready`→`error` minimum set.
- Summary generation pipeline is transcript + metadata grounded.
- Legacy `/demo*` and `/visual-session*` routes are absent from active UX.
