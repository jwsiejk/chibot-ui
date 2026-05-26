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

### 6.1 Deck Brief schema contract (v1.0)

This contract is implementation-oriented for initial phases and may evolve only through explicit schema versioning.

```json
{
  "schema_version": "1.0",
  "mode": "create_presentations",
  "deck_type": "customer_executive_briefing",
  "topic": "non-empty string",
  "audience": "non-empty string",
  "customer_context": "string",
  "industry": "string",
  "use_case": "string",
  "slide_count": 12,
  "tone": "technical_but_executive_readable",
  "technical_depth": "medium",
  "must_include": ["string"],
  "user_notes": "string",
  "constraints": ["string"],
  "required_messaging": ["string"],
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

Required fields:
- `schema_version`
- `mode`
- `deck_type`
- `topic`
- `audience`
- `slide_count`
- `tone`
- `technical_depth`
- `source_requirements.source_policy`
- `output.format`
- `output.speaker_notes`
- `status`

Optional but supported fields:
- `customer_context`
- `industry`
- `use_case`
- `must_include`
- `user_notes`
- `constraints`
- `required_messaging`

Controlled enum values:
- `mode`: `create_presentations`
- `deck_type`: `customer_executive_briefing`, `customer_technical_deep_dive`, `partner_enablement`, `internal_training`, `architecture_review`, `workshop`, `roadmap`, `proposal`, `custom`
- `tone`: `executive`, `consultative`, `technical`, `technical_but_executive_readable`, `sales`, `training`, `concise`, `custom`
- `technical_depth`: `low`, `medium`, `high`, `mixed`
- `source_requirements.source_policy`: `user_provided_only`
- `source_requirements.allowed_source_types`: `manual_notes`, `uploaded_later`
- `output.format`: `pptx`
- `status`: `draft`, `brief_review`, `brief_approved`, `outline_draft`, `outline_review`, `outline_approved`, `generation_ready`, `generated`, `error`

Validation rules for initial phases:
- `topic` must be non-empty.
- `audience` must be non-empty.
- `slide_count` must be an integer between 3 and 30.
- `must_include` must be an array of strings if provided.
- `output.speaker_notes` must be boolean.
- `source_requirements.citations_required` must remain `false` while `source_policy` is `user_provided_only`.
- No internal/private source citations are allowed until shared retrieval/RAG is explicitly approved in a later phase.
- Outline generation cannot proceed until `status` is `brief_approved`.
- PPTX generation cannot proceed until `status` is `outline_approved` or `generation_ready`.

### 6.2 Example Deck Brief payload

```json
{
  "schema_version": "1.0",
  "mode": "create_presentations",
  "deck_type": "customer_executive_briefing",
  "topic": "Q3 storage modernization proposal",
  "audience": "CIO and infrastructure leadership",
  "customer_context": "Large healthcare provider evaluating AI + backup refresh",
  "industry": "healthcare",
  "use_case": "hybrid AI and cyber resilience",
  "slide_count": 10,
  "tone": "technical_but_executive_readable",
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
  "constraints": [
    "Keep content executive-readable",
    "Limit each slide to one primary message"
  ],
  "required_messaging": [
    "Position modernization as risk reduction and business enablement"
  ],
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

### 6.3 Deck Brief lifecycle and stage gating

Expected forward lifecycle:
`draft` → `brief_review` → `brief_approved` → `outline_draft` → `outline_review` → `outline_approved` → `generation_ready` → `generated`

Failure behavior:
- `error` may be used for explicit failures at any stage.
- The app must not silently skip failed stages.

### 6.4 Implementation notes for Phase 2

- Phase 2 should implement validation against this schema contract.
- Invalid Deck Brief data should produce explicit user-facing guidance.
- The Deck Brief should be treated as the boundary object between UI state, guided interview, outline generation, and future PPTX generation.
- Do not hardcode future RAG assumptions into the schema.
- Future retrieval fields should be added by schema versioning only after approval.



### 6.5 Phase 2B hardening notes

- Optional Deck Brief fields may be intentionally skipped by the user using natural skip language (for example: `skip`, `none`, `n/a`, `leave blank`).
- Implementations may track skipped optional fields in presentation-mode state so review rendering can show `Skipped` without persisting placeholder text.
- Enum prompts should present friendly labels and accept natural-language equivalents, while still normalizing to the schema enum values.
- In `brief_review`, users may request direct field revisions (for supported fields) and the system should re-render the full Deck Brief review until approval.

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
- Generate editable PPTX from approved outline using a production PPTX library (not hand-built Open XML assembly and not shelling to system `zip`).
- Treat the approved outline as immutable input for this phase; do not regenerate or reinterpret outline content during PPTX generation.
- Keep Deck Brief lifecycle focused on brief/outline states (`outline_approved` remains after generation); track generation lifecycle under `generatedPresentation`.
- Speaker notes are included only when supported by the selected PPTX library/runtime path.
- Deferred scope remains deferred in Phase 4 (no RAG, no Glean, no DDN retrieval/content lookup).

Implementation note: Phase 4 must use a real PPTX library runtime path (for example, `pptxgenjs`) and must not hand-build Open XML parts or shell out to system archiving tools.
If speaker notes are not proven reliable in the selected runtime path, they remain deferred rather than faked.
Current implementation note: generated PPTX downloads are exposed through a real runtime route at `/api/presentations/:fileName` (wired in Vite dev/preview middleware) backed by local API helper/service logic. The route serves PPTX bytes with PPTX MIME type and attachment headers, rejects invalid or traversal-style filenames (including encoded traversal/absolute-path attempts), and only resolves files inside the generated presentations output directory.

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


### 6.6 Phase 3 implementation clarification (hardening)

- Phase 3 may synchronously generate and immediately present an outline after brief approval.
- In that synchronous path, `outline_draft` is a lifecycle concept but is not persisted as a long-lived user-visible state.
- `outline_review` is the persisted review state after generation and before approval.
- Phase 3 scope still stops at `outline_approved` (no PPTX generation/export behavior in Phase 3).
- Phase 4 remains the first phase for PPTX generation work.
- Shared retrieval/RAG/Glean/DDN content integration remains deferred to later phases.

## Phase 4 implementation note

- Phase 4 generates an editable `.pptx` only after both Deck Brief and Outline are `outline_approved`.
- The approved outline is immutable input for PPTX generation: slide count, titles, objectives, and key points are consumed as-is with no redesign/regeneration.
- Presentation generation metadata should be tracked in mode state as `generatedPresentation` with status, file name/path, download URL, timestamp, and explicit error message fields.
- Export/download access should be restricted to the generated presentations output directory with filename validation and path traversal rejection.
- Retrieval features remain deferred in Phase 4 (no RAG, Glean, DDN content repo, embeddings, ingestion, or citations).
