# AskChip Local v1 Contract

This markdown file is the reviewable, authoritative AskChip Local v1 contract artifact in the repo root.
The legacy `AskChip Local v1 Contract.docx` export remains in the repository, but contract changes for code review and pull requests must be captured here in text form.

## Canonical transcript contract
- Text is the source of truth.
- The canonical transcript field is `text`, never `content`.
- `role` is speaker identity.
- `source` is origin semantics, not speaker identity.
- AskChip uses one unified canonical transcript for typed chat, push-to-talk, streaming assistant output, and speech playback alignment.
- Do not invent alternate frontend-only message shapes.

## Allowed top-level session states
The only allowed top-level states are:
- `ready`
- `listening`
- `transcribing`
- `thinking`
- `speaking`
- `error`

## Session creation metadata (optional)
- `POST /api/v1/sessions` may include an optional typed `metadata` object.
- For Expert Desk frontstage handoff, session creation may include `metadata.expert_desk` as session-scoped pre-brief context.
- Expert Desk persona routing metadata is canonicalized with:
  - `expert_persona_id` (stable routing identity)
  - `expert_persona_label` (display label)
  - optional `expert_persona_summary` (descriptive helper text)
- Expert Desk metadata may also include uploaded-log summary fields for runtime-aware triage:
  - `uploaded_logs_count` (number)
  - `uploaded_log_names` (string array)
  - `uploaded_logs_available` (boolean)
  - optional `recommended_vmware_logs` (string array guidance list)
- Expert Desk metadata may include typed VMware triage state under `metadata.expert_desk.vmware_triage`:
  - `issue_family`
  - `suspected_layer`
  - `impact_scope`
  - `recent_change_summary`
  - `symptom_summary`
  - `open_questions` (string array)
  - `confidence` (0.0 - 1.0)
  - `conversation_stage`
  - `policy_next_move`
  - `next_best_question`
  - `required_logs` (string array)
  - `received_logs` (string array)
  - `missing_logs` (string array)
  - `log_sufficiency_status`
  - `optional_logs` (string array)
  - `log_guidance_summary`
  - `resolution_status`
  - `last_updated_from_turn_id`
- `vmware_triage.resolution_status` is normalized to:
  - `unresolved`
  - `monitoring`
  - `resolved`
  - `blocked_waiting_on_logs`
  - `blocked_waiting_on_user_action`
  - `needs_human_handoff`
- Expert Desk metadata may include typed VMware handoff packet state under `metadata.expert_desk.vmware_handoff`:
  - `issue_summary`
  - `working_hypothesis`
  - `confirmed_facts` (string array)
  - `open_questions` (string array)
  - `actions_taken` (string array)
  - `logs_received` (string array)
  - `logs_missing` (string array)
  - `log_sufficiency_status`
  - `current_resolution_status`
  - `recommended_next_step`
  - `handoff_reason`
  - `ready_for_handoff` (boolean)
- This metadata is stored on the session record and is available before the first assistant turn.
- Session metadata updates (`PATCH /api/v1/sessions/{session_id}`) may update `metadata.expert_desk` during live sessions (for example when new log-file metadata is added), and this runtime metadata is used for later typed + voice turns.
- During live turn runtime, AskChip may use session-scoped `metadata.expert_desk` as prompt preface/system-context pre-briefing before transcript history and current user turn (typed and voice paths), without changing stored transcript message shape.
- For VMware Expert Desk sessions, AskChip may run a hidden extraction step after each committed user turn and before assistant generation to update typed `metadata.expert_desk.vmware_triage` state; invalid or low-confidence extraction output must not overwrite prior triage state.
- For mapped VMware issue families, AskChip may deterministically evaluate uploaded log metadata names against a requirement matrix and persist log sufficiency metadata (`log_sufficiency_status`, `required_logs`, `received_logs`, `missing_logs`, `optional_logs`, `log_guidance_summary`) without claiming parsed-log findings.
- During those VMware PATCH-time log-sufficiency refreshes, AskChip may also recompute and persist deterministic policy fields (`policy_next_move`, policy-aligned `conversation_stage`, and `next_best_question`) from the current typed triage state with non-regressive behavior, avoiding resets to `confirm_issue_family`/`issue_definition` when the issue family path is already established.
- VMware triage/policy refreshes may also refresh `metadata.expert_desk.vmware_handoff` so summary/handoff flows use current persisted triage, transcript-derived facts, and uploaded log-name metadata without claiming parsed-log conclusions.
- Runtime persona overlay selection must use `expert_persona_id` first; legacy prose-only fields may be used only as backward-compatible fallback.
- Canonical transcript rules remain unchanged: transcript messages still use `text` (never `content`), with `role` as speaker identity and `source` as origin semantics.
- `CreateTurnRequest` and transcript message shape are unchanged.

## Assistant speech contract
- Assistant speech is derived from the same canonical assistant message that is shown in the transcript.
- Speech may begin before the full assistant message is complete, as soon as a stable sentence-level chunk is available from that canonical assistant message.
- Earlier first audio must not be implemented by increasing playback speed.
- The configured Kokoro TTS speed remains unchanged.
- Chunking should prefer complete sentences or strong natural pause boundaries and should avoid tiny chopped fragments.
- Already spoken text must not be repeated.
- Only one assistant playback may be active per session.
- If a spoken chunk ends while generation is still ongoing and no next stable chunk exists yet, session state may move from `speaking` back to `thinking` while waiting for the next chunk.
- When generation is complete and all spoken content is complete, session state returns to `ready`.
- Interrupt on typed submit and push-to-talk must still stop active playback promptly.

## TTS text handling
- TTS sanitization applies only to the text sent to speech synthesis.
- Canonical transcript storage must remain unchanged.
- Simple stage directions such as `[laughs]`, `(pause)`, `*chuckles*`, and `[sigh]` are stripped or converted into natural punctuation only for spoken output.
- AskChip continues to use plain-text Kokoro TTS only.
- No SSML is added.
- No injected laugh, chuckle, or other reaction audio clips are added.

## Marlene persona
Marlene remains a `middle-aged Nebraska farmer turned tech geek`.
She should feel warm, plainspoken, grounded, capable, conversational, and human, with helpfulness first and personality second.
She should read the user’s tone naturally, stay shorter by default, avoid stiff or over-explanatory answers unless asked, and avoid stage directions or reaction markers.

## WebRTC and scope boundaries
- WebRTC remains diagnostics-only and is not required for typed chat, push-to-talk commit, or TTS playback.
- The following remain out of scope: wake word, always-open mic, VAD-owned turn commit, tools, RAG, auth/admin work, cloud sync, and Docker.
