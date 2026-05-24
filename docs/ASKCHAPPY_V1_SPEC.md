# AskChappy V1 Specification

## 1) Product purpose
AskChappy is a Zoom-like AI partner enablement experience for DDN. Chappy acts as a virtual Partner Technical Manager (vPTM) to help partners and internal teams prepare, communicate, and execute high-quality DDN conversations.

## 2) Target users
- DDN partner sellers
- DDN partner systems engineers (SEs)
- Internal DDN partner-facing teams

## 3) Chappy persona definition
Chappy is a virtual Partner Technical Manager for DDN. He helps build and enable the DDN partner community. He is practical, personable, technically credible, and partner-focused. He translates DDN technical value into clear partner/customer conversations. He is not a generic assistant and not a support-ticket bot.

## 4) Default Open Q&A behavior
- Every new session defaults to **Open Q&A** (“Ask Chappy anything”).
- Guided modes are optional overlays, not mandatory entry gates.
- Chappy may suggest guided modes during a session when useful.
- Chappy responses are voice-first by default: concise, conversational, and speakable.
- Default response target is 2–4 short sentences (~40–90 words), with one idea at a time.
- Deep dives are opt-in: Chappy should offer to go one layer deeper instead of dumping long markdown/bullets by default.
- Ask at most one follow-up question per turn unless the user explicitly requests a broader checklist/breakdown.
- Content grounding/RAG/DDN document ingestion is deferred, so product-specific internals should be framed carefully and not overclaimed.

## 5) Guided modes (V1)
- Open Q&A (default)
- Learn DDN
- Customer / Partner Meeting Prep
- Pitch Practice
- Objection Handling
- Competitive Positioning
- Technical Deep Dive
- Follow-up Builder

## 6) Zoom-like UI requirements
- Session should feel like joining a focused Zoom-like working room.
- Chappy is visually centered as the primary stage participant.
- Chat panel and transcript should remain available throughout the session.
- UI should avoid support-ticket or triage-form framing as primary UX.

## 7) Session lifecycle
1. User enters Chappy room (Open Q&A default).
2. User asks by typed or spoken input.
3. Chappy responds with unified transcript-first output.
4. Optional guided mode is suggested/applied when beneficial.
5. Session ends with recap-ready transcript and action artifacts.

## 8) Canonical transcript / chat / voice contract
- One canonical transcript for all user and assistant turns.
- `text` is the canonical message field (not `content`).
- `role` captures speaker identity (`user`, `assistant`, `system`).
- `source` captures modality/origin (`typed`, `voice`, `assistant_stream`, `speech`, `system`, `summary`).
- Typed chat and spoken input must produce the same transcript shape.
- Spoken Chappy output must be derived from the exact assistant transcript message shown in text.
- No voice-only response is allowed without transcript text.
- No chat-only response is allowed that cannot be spoken later.
- Transcript powers chat rendering, voice playback alignment, recap, and future memory.
- Internal app events such as mode changes may inform recap/diagnostics but are not automatically visible chat transcript messages.

## 9) Session state model
Minimum V1 state model:
- `ready`
- `listening`
- `transcribing`
- `thinking`
- `speaking`
- `error`

## 10) Voice / TTS requirements
- Voice is a modality over the same canonical transcript.
- TTS architecture must support provider abstraction/switching:
  - local/simple TTS provider (development fallback)
  - Chappy cloned-voice provider (future)
  - optional premium/cloud provider (future)
- Chappy voice clone is a future requirement and not implemented in this docs-only phase.

## 11) Avatar requirements
- Chappy should be represented in the Zoom-like central stage.
- Initial implementation may use a placeholder silhouette/avatar.
- Architecture must support future replacement with real Chappy avatar assets.
- Required visual states: idle, listening, thinking, speaking.
- Should allow future speaking animation/viseme support.

## 12) Summary / recap requirements
- Session recap must be generated from canonical transcript plus session metadata.
- Outputs may include notes, action items, talk tracks, and follow-up content.
- Recap is a partner enablement artifact, not a support-case ticket handoff.

## 13) Out-of-scope for V1
- Re-implementing AskChip Local / Expert Desk / VMware triage UX.
- Deterministic support-routing workflows.
- Voice-clone model training pipeline in public repo.
- Production avatar generation pipeline in public repo.
- Proprietary/private DDN content bundles in public repo.

## 14) Acceptance criteria for first runnable MVP
- User can start Open Q&A session immediately.
- Typed and spoken user input both append to one canonical transcript.
- Assistant streaming and final response both map to canonical transcript entries.
- Chappy response is shown in text and can be spoken from the same message.
- Basic mode switching overlays can be applied without changing bot identity.
- Session states (`ready` through `error`) are visible and coherent.
- Session recap can be generated from canonical transcript.


## 15) Implementation contracts handoff
- Implementation must follow `docs/IMPLEMENTATION_CONTRACTS.md` for route map, session metadata, transcript schema, state machine, and summary contracts.
- Canonical V1 user flow is `/chappy` and `/chappy/session/:sessionId` with recap on `/chappy/summary/:sessionId`.
- Legacy `/demo*` and `/visual-session/:sessionId` routes remain retired history only (see `docs/ASKCHIP_REFERENCE_NOTES.md`).

## 16) MVP login and role model
- `/chappy` begins with an email-only login modal.
- No password is required for local MVP.
- Email is used for local-first role selection and personalization only.
- This is email-only local auth for the current local production MVP; future enterprise auth may replace it.

Role mapping rule (MVP):
- `jsiejk@ddn.com` => `admin`
- all other emails => `standard_user`

## 17) Admin routes and visibility
Planned routes:
- `/admin`
- `/admin/voice`
- `/admin/avatar`

Rules:
- Admin routes are hidden unless logged in as admin.
- Standard users must not see admin navigation.
- Standard-user access to admin routes returns a simple “not authorized” state.
- No voice cloning controls appear in normal `/chappy/session/:sessionId` sessions.

## 18) Voice Studio (admin-only planned workflow)
Voice Studio is planned workflow, not implemented in this docs-only phase:
1. Admin opens `/admin/voice`.
2. Admin records/uploads voice samples.
3. System creates draft profile.
4. Admin tests generated speech.
5. Admin approves profile.
6. Admin publishes as global AskChappy voice.
7. Standard users hear published voice in future sessions.

Voice profile lifecycle states:

```text
draft
testing
approved
published
disabled
```

## 19) MVP consent note
- Chapman should approve usage of his voice for AskChappy.
- For MVP, lightweight admin confirmation is sufficient (example: “I confirm Chapman approved using this voice for AskChappy.”).
- Do not over-engineer legal/security workflow for this docs-only MVP scope.
