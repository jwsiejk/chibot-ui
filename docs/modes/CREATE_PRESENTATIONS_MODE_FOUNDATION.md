# Create Presentations Mode Foundation (Pre-Implementation)

## 1) Purpose and scope of this document

This document defines the product and architecture foundation for a future AskChappy guided mode: **Create Presentations**.

This is a documentation-only foundation. No runtime behavior, dependencies, retrieval integrations, or implementation code are introduced by this document.

## 2) Product overview

Create Presentations Mode is a dedicated AskChappy mode where vChappy guides a user through building a professional presentation.

Core definition:
- Dedicated AskChappy mode (entered from the Modes toolbar).
- Purpose-built for work/customer/technical/business presentation creation.
- Guided by vChappy via a structured interview flow.
- Behaviorally separate from normal open-ended chat while active.
- DDN-focused long term, but initial phases use only user-provided input and notes.

## 3) Non-goals / deferred scope

The following capabilities are explicitly deferred and must not be implemented in initial Create Presentations phases:
- DDN content repository integration.
- Glean API integration.
- RAG pipelines.
- Embeddings/vector database infrastructure.
- Document ingestion pipelines.
- Source-grounded retrieval.
- Citations from internal/private content.
- Customer/account content lookup.
- Fully autonomous “one prompt creates perfect deck” behavior.
- Polished visual preview UI unless intentionally added in a later phase.

Cross-mode direction:
- If/when DDN/Glean/retrieval is introduced, it should become a **shared AskChappy capability across modes**, not a presentation-only hardcoded path.

## 4) Intended user flow

1. User selects **Modes → Create Presentations**.
2. AskChappy clearly enters Create Presentations mode state.
3. vChappy gives an initial mode-specific greeting and explains the guided process.
4. vChappy runs a structured interview (guided questions).
5. System builds a structured Deck Brief object.
6. User reviews/refines the Deck Brief.
7. vChappy generates a proposed outline from the approved brief.
8. User approves or revises outline.
9. Later phase: system generates editable PPTX from approved outline.
10. User exports/downloads generated presentation artifacts.
11. User exits mode and returns to normal AskChappy behavior.

## 5) Guided interview fields

vChappy should collect at least the following fields before outline generation:
- Presentation topic.
- Audience.
- Customer/company context.
- Industry and use case.
- Deck type.
- Desired slide count.
- Tone/style.
- Technical depth.
- Must-have sections.
- Source material / manual user notes.
- Output format.
- Speaker notes required (`yes`/`no`).
- Constraints and required messaging.

## 6) Deck Brief contract (schema + example)

The Deck Brief is the contract between:
1) guided conversation,
2) outline generation, and
3) PPTX generation.

### 6.1 Initial JSON schema (draft)

```json
{
  "mode": "create_presentations",
  "deck_type": "string",
  "topic": "string",
  "audience": "string",
  "customer_context": "string",
  "industry": "string",
  "use_case": "string",
  "slide_count": 12,
  "tone": "string",
  "technical_depth": "string",
  "must_include": ["string"],
  "user_notes": "string",
  "source_requirements": {
    "source_policy": "user_provided_only",
    "citations_required": false,
    "allowed_source_types": ["manual_notes", "uploaded_later"]
  },
  "output": {
    "format": "pptx",
    "speaker_notes": true
  },
  "status": "draft"
}
```

### 6.2 Example Deck Brief payload

```json
{
  "mode": "create_presentations",
  "deck_type": "customer_executive_update",
  "topic": "Q3 storage modernization proposal",
  "audience": "CIO and infrastructure leadership",
  "customer_context": "Large healthcare provider evaluating AI + backup refresh",
  "industry": "healthcare",
  "use_case": "hybrid AI and cyber resilience",
  "slide_count": 10,
  "tone": "confident and consultative",
  "technical_depth": "medium",
  "must_include": [
    "business drivers",
    "current-state challenges",
    "target architecture",
    "implementation phases",
    "risks and mitigations",
    "next steps"
  ],
  "user_notes": "Emphasize operational simplicity and measurable timeline.",
  "source_requirements": {
    "source_policy": "user_provided_only",
    "citations_required": false,
    "allowed_source_types": ["manual_notes"]
  },
  "output": {
    "format": "pptx",
    "speaker_notes": false
  },
  "status": "brief_review"
}
```

## 7) Intended architecture (modular)

Create Presentations mode should be added as modular components that remain separated from normal chat logic:
- Modes toolbar entry (`Create Presentations`).
- Create Presentations mode state handler.
- Presentation mode prompt/service module.
- Deck Brief builder module.
- Outline generator module.
- Future PPTX generator module.
- Future template/theme system module.
- Future shared retrieval/content service module (cross-mode, later phase).

Architecture rule:
- Presentation-specific behavior should remain isolated from base open-chat behavior to avoid regressions and mode coupling.

## 8) Implementation phase plan

### Phase 1 — Mode shell + state framework
- Add mode enumeration and UI shell/state boundaries only.
- No outline/PPTX generation yet.

### Phase 2 — Guided interview + Deck Brief creation
- Add structured Q&A collection flow.
- Persist/maintain Deck Brief draft and review state.

### Phase 3 — Outline generation + approval loop
- Generate outline from approved Deck Brief.
- Support user revise/approve loop.

### Phase 4 — PPTX generation
- Generate editable PPTX from approved outline.

### Phase 5 — Template/theme/speaker-notes/export polish
- Add visual/theme/template controls.
- Improve export UX and optional speaker-note shaping.

### Phase 6+ — Shared DDN/Glean/retrieval integration across modes
- Add shared retrieval layer only after explicit approval.
- Keep retrieval capability reusable by all AskChappy modes.

## 9) Acceptance criteria by phase

### Phase 1 acceptance
- Create Presentations appears in Modes selector.
- Enter/exit mode changes mode state cleanly.
- No normal-chat behavior regressions in `open_qa`.
- No new dependencies.

### Phase 2 acceptance
- Guided interview captures required fields.
- Deck Brief object validates against agreed contract.
- User can review and edit captured brief data.

### Phase 3 acceptance
- Outline generated only from Deck Brief + user input.
- User can revise/approve outline in deterministic flow.
- No fake sourcing or hidden retrieval.

### Phase 4 acceptance
- Editable PPTX output is produced from approved outline.
- Output contract (format + notes option) is honored.
- Failures are explicit (no fake success output).

### Phase 5 acceptance
- Templates/themes behave predictably.
- Speaker notes toggle is honored.
- Export workflow is clear and non-destructive.

### Phase 6+ acceptance
- Retrieval is implemented once as cross-mode shared service.
- Source policy, attribution, and trust boundaries are explicit.
- Retrieval can be disabled without breaking presentation mode core flow.

## 10) Guardrails

- No hidden fallbacks.
- No mock presentation generation represented as real output.
- No fake source citations.
- No RAG/retrieval until explicitly approved.
- Keep mode logic modular and isolated.
- Do not bloat existing large files; prefer focused modules.
- Preserve normal AskChappy chat behavior outside this mode.
- Every implementation phase must include tests.

## 11) Future technical direction (non-binding)

Potential future directions (not vendor-locked commitments):
- Editable PPTX generation via **PptxGenJS** or equivalent.
- Presenton-inspired preview/workflow UX patterns.
- Local LLM-assisted planning/outline/content expansion.
- Shared retrieval layer later for DDN/Glean content across modes.

These are directional options only and require explicit approval at implementation time.
