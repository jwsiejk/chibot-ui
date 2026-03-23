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
