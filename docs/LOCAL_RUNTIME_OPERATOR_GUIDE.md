# AskChappy Local Runtime Operator Guide (Phase 20B)

This runbook is for local operators validating and troubleshooting the full AskChappy local-first runtime.

## Scope and guardrails
- AskChappy is local-first production software for DDN partner enablement workflows.
- This guide covers operation/validation only; it does not add product scope.
- No cloud/OpenAI/hosted providers are used in this workflow.

## Required local services
Run all of these locally:
1. Ollama runtime
2. Kokoro/kokoro-onnx TTS runtime
3. faster-whisper STT runtime
4. AskChappy Vite runtime (`npm run start`)

## Required/default local endpoints
Use these defaults unless intentionally overridden:

- `OLLAMA_BASE_URL=http://127.0.0.1:11434`
- `OLLAMA_MODEL=gemma3:4b`
- `KOKORO_TTS_BASE_URL=http://127.0.0.1:8880`
- `KOKORO_TTS_VOICE=af_sarah`
- `KOKORO_TTS_FORMAT=wav`
- `FASTER_WHISPER_BASE_URL=http://127.0.0.1:8890`
- `FASTER_WHISPER_MODEL=base.en`
- `FASTER_WHISPER_LANGUAGE=en`

Optional runtime knobs may also be used where supported (for example timeout/context settings), but defaults above define the baseline operator validation loop.

## Start order (required)
Always start in this order:
1. Start Ollama.
2. Ensure/pull configured Ollama model (`gemma3:4b` by default).
3. Start Kokoro/kokoro-onnx local TTS service.
4. Start faster-whisper local STT service.
5. Start AskChappy app:
   ```bash
   npm run start
   ```

## Preflight validation checklist
Before a user session, verify:
- All four required local services are running.
- `/chappy` is reachable from the browser.
- Browser microphone permission can be granted.
- Local Runtime Readiness panel reports expected local runtime states.
- Standard local voice is selected/default.
- Cloned voice is optional/gated and not required for the normal loop.

## Full runtime validation pass
Execute this validation pass end-to-end:
1. Open `http://127.0.0.1:4173/chappy`.
2. Start a session.
3. Review the Local Runtime Readiness panel and confirm local service visibility.
4. Type a message and submit.
5. Speak through the browser microphone.
6. Confirm voice input appends canonical user transcript `text` with `source: voice`.
7. Confirm assistant response appears as canonical assistant transcript `text`.
8. Trigger **Speak response** and confirm standard local voice playback.

Expected behavior constraints:
- Transcript uses `text`, never `content`.
- STT/TTS/readiness checks do not append fake transcript messages.
- Session events remain separate from transcript content.

## Troubleshooting

### Ollama unreachable
Symptoms:
- Runtime readiness marks Ollama runtime unreachable.
- Assistant typed response path fails with local runtime errors.

Actions:
- Confirm Ollama process is running locally.
- Confirm `OLLAMA_BASE_URL` points to reachable local endpoint.
- Re-run validation after runtime comes online.

### Ollama model unavailable
Symptoms:
- Ollama runtime reachable, but model readiness fails.

Actions:
- Pull or prepare configured model (`gemma3:4b` default).
- Confirm `OLLAMA_MODEL` matches an installed local model.
- Re-check readiness panel after model availability is restored.

### Kokoro health unavailable
Symptoms:
- TTS readiness cannot confirm via `/health` or `/v1/health`.

Actions:
- Confirm Kokoro/kokoro-onnx service is running and reachable at `KOKORO_TTS_BASE_URL`.
- Validate local endpoint path support in your Kokoro runtime.

### Kokoro synthetic readiness fallback
Behavior:
- If health endpoints are unsupported, readiness may use a fixed synthetic `/v1/tts` probe.
- Probe uses non-user fixed text and discards output.

Actions:
- Treat this as readiness compatibility behavior, not user transcript activity.
- Confirm no synthetic probe text/audio appears in transcript.

### faster-whisper unreachable
Symptoms:
- STT readiness fails or voice transcription cannot start.

Actions:
- Confirm faster-whisper service is running locally.
- Confirm `FASTER_WHISPER_BASE_URL` resolves locally.
- Re-test microphone flow.

### Microphone denied/unavailable
Symptoms:
- Browser reports denied permission or missing input device.

Actions:
- Grant microphone permission for local runtime origin.
- Verify active microphone device selection in OS/browser settings.
- Retry voice capture.

### TTS unavailable
Symptoms:
- Assistant transcript appears, but speech playback fails.

Actions:
- Confirm Kokoro service availability and voice config (`af_sarah`, `wav` defaults).
- Verify TTS endpoint connectivity from local app runtime.

### STT no speech
Symptoms:
- Voice capture occurs but no transcript text is returned.

Actions:
- Check microphone level/input quality.
- Reduce background noise and retry with short clear utterance.
- Confirm faster-whisper runtime health and model/language configuration.

### Browser local persistence reset or malformed localStorage
Symptoms:
- Session history missing after reload or persistence recovery message/behavior.

Actions:
- Confirm localStorage availability in browser profile.
- If malformed persisted payload was present, allow safe reset/recovery path.
- Recreate session and re-validate transcript/metadata behavior.

## What not to expect (out of scope)
- No RAG/content grounding/DDN document ingestion.
- No cloud fallback or hosted providers.
- No cloned voice provider adapter runtime.
- No real avatar/visemes.
- No database/cloud persistence.

## Related docs
- Local-first run guide: `docs/LOCAL_FIRST_RUN_GUIDE.md`
- Local-first release checklist: `docs/LOCAL_FIRST_RELEASE_CHECKLIST.md`
- Current implementation status: `docs/CURRENT_IMPLEMENTATION_STATUS.md`
- Implementation contracts: `docs/IMPLEMENTATION_CONTRACTS.md`
