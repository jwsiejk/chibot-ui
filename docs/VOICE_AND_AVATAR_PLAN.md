# Voice and Avatar Plan

## Voice cloning (future requirement)
- AskChappy should support future cloning of Chapman’s voice for Chappy delivery.
- This capability is planned, not implemented in the docs-only cleanup.

## Consent requirement
- Voice clone creation and usage require Chapman’s explicit consent.
- Consent records/process should be documented before any clone training or deployment.

## Sample recording guidance (future)
- Collect clear, diverse, consented recordings with representative speaking styles.
- Use controlled capture quality and labeling for downstream provider compatibility.
- Keep all raw recordings outside the public repository.

## Private asset handling
- Do not commit voice samples, embeddings, model artifacts, or private likeness files without explicit approval.
- Configure app/runtime to reference private local or secured storage paths.

## Provider abstraction
- TTS/voice stack should be provider-agnostic with adapter boundaries.
- Planned providers:
  - Local/simple development TTS fallback
  - Chappy cloned-voice provider
  - Optional premium/cloud provider

## Avatar evolution plan
1. Placeholder silhouette/avatar
2. Static branded Chappy image
3. Animated state avatar (idle/listening/thinking/speaking)
4. Speaking/viseme-capable avatar layer

## Asset safety rule
Do not commit real voice samples, trained voice artifacts, or private avatar likeness assets to the public repo unless explicitly approved.


## Implementation contract alignment
- Voice and avatar integrations must attach to the canonical transcript and session states defined in `docs/IMPLEMENTATION_CONTRACTS.md`.
- No voice/avatar feature may introduce a parallel message model or bypass route/session contracts.

## Admin-only Voice Studio plan (MVP architecture)
- Voice Studio is admin-only and planned as workflow documentation, not implemented runtime code in this repo state.
- Purpose is product/UX control of the shared Chappy voice, not heavy security hardening.

Planned workflow:
1. Admin opens `/admin/voice`.
2. Admin records or uploads Chappy voice samples.
3. System creates `draft` voice profile.
4. Admin test-generates sample speech (`testing`).
5. Admin approves profile (`approved`).
6. Admin publishes profile globally (`published`).
7. Standard users hear the published voice in future `/chappy/session/:sessionId` sessions.

Lifecycle states:

```text
draft
testing
approved
published
disabled
```

Boundary rule:
- Voice cloning is not part of normal Zoom-like user session flow.
- User sessions consume only the currently published voice profile.

