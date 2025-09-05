# Phase 8 — Acceptance Checklist (Retrieval & Persona Prompt)

1. DAL has KB tables or in-memory fallback; retrieval service exposes add_document() and search().
2. Admin seed endpoint exists: POST /api/v1/admin/kb/seed {title, body, tags} → {ok, doc_id}.
3. streaming.make_assistant_frames() fetches top-K KB snippets and passes them into provider context.
4. Providers handle context (mock shows [KB:N], OpenAI stub includes KB:N).
5. TTS stays MP3 path (ElevenLabs output format defaults to mp3 if unset; env `ELEVEN_OUTPUT_FORMAT` supported).
6. All previous phase tests + route linter still pass.
