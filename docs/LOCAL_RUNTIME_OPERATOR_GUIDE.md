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

Env-file behavior: `.env.local` values are loaded by Vite (`loadEnv`) and only these local runtime keys are surfaced to runtime config helpers through `process.env.*` constants. This keeps config local-first and avoids introducing cloud/provider secret paths.

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

### faster-whisper `/health` is 200 but `/v1/transcribe` is 500
Symptoms:
- `GET /health` reports ready.
- Browser `POST /v1/transcribe` fails with HTTP 500.
- AskChappy shows STT transcription failure messaging.

Meaning:
- Service is reachable, but transcription processing failed.

Actions:
- Check the faster-whisper service PowerShell/terminal window for traceback details.
- Confirm browser-uploaded audio container/codec is supported by your local decoding stack.
- Check for decode failures, malformed upload payloads, and CUDA/model load/runtime errors.
- Retry with a short clear utterance and verify `FASTER_WHISPER_MODEL`/runtime dependencies.

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

## Windows startup scripts
- `.\scripts\check-local-runtime.ps1` checks Ollama runtime/model, Kokoro health (`/health` then `/v1/health`), faster-whisper `/health`, and prints a status table.
- If Kokoro health endpoints are unsupported, the check script reports that AskChappy runtime may still use synthetic readiness fallback, but the script itself does not synthesize audio.
- `.\scripts\start-kokoro-tts.ps1` and `.\scripts\start-faster-whisper-stt.ps1` require local runner command variables in `.env.local` (`KOKORO_TTS_RUN_COMMAND`, `FASTER_WHISPER_RUN_COMMAND`).
- `.\scripts\start-local-runtime.ps1` enforces startup sequence: Ollama -> Kokoro -> faster-whisper -> `npm run start`.
- Use `nvidia-smi -l 1` manually for GPU monitoring; AskChappy has no native Windows GPU process helper yet.

## Phase 21C local runtime venv/env handling
- `.venv-local-runtime` is the dedicated local-only Python virtual environment for local Kokoro/faster-whisper GPU dependency setup and must not be committed.
- `.env.local` remains machine-local and gitignored. `.env.example` stays committed and secret-free.
- Keep runner commands machine-local in `.env.local`:
  - `KOKORO_TTS_RUN_COMMAND=...`
  - `FASTER_WHISPER_RUN_COMMAND=...`
- Local model/audio assets remain outside git (example: `C:\AskChipAssets\kokoro\kokoro-v1.0.onnx`, `C:\AskChipAssets\kokoro\voices-v1.0.bin`).

Create/setup from repo root:
```powershell
py -m venv .venv-local-runtime
.\.venv-local-runtime\Scripts\activate
python -m pip install --upgrade pip setuptools wheel
pip uninstall -y onnxruntime onnxruntime-gpu
pip install onnxruntime-gpu kokoro-onnx faster-whisper
```

Validation commands:
```powershell
python -c "import onnxruntime as ort; print(ort.get_available_providers())"
python -c "import kokoro_onnx; print('kokoro_onnx import OK')"
python -c "from faster_whisper import WhisperModel; print('faster-whisper import OK')"
```

Expected ONNX providers include:
```text
CUDAExecutionProvider
CPUExecutionProvider
```

Important: package installation alone does not start services. AskChappy still requires local HTTP runtimes:
- Kokoro TTS: `http://127.0.0.1:8880`
- faster-whisper STT: `http://127.0.0.1:8890`

## Phase 21D wrapper runtime details
- Local HTTP wrapper services are committed under `services/local-runtime/`:
  - `kokoro_tts_server.py` (`GET /health`, `GET /v1/health`, `POST /v1/tts`)
  - `faster_whisper_stt_server.py` (`GET /health`, `POST /v1/transcribe`)
- Create/install local-only Python runtime:
```powershell
py -m venv .venv-local-runtime
.\.venv-local-runtime\Scripts\activate
python -m pip install --upgrade pip setuptools wheel
pip install -r services/local-runtime/requirements.txt
```
- Validate CUDA provider visibility before startup:
```powershell
python -c "import onnxruntime as ort; print(ort.get_available_providers())"
```
- Start commands:
  - `./scripts/start-kokoro-tts.ps1`
  - `./scripts/start-faster-whisper-stt.ps1`
  - `./scripts/check-local-runtime.ps1`
  - `./scripts/start-local-runtime.ps1`
- Model/voice assets stay outside repo (`C:\AskChipAssets\kokoro\kokoro-v1.0.onnx`, `C:\AskChipAssets\kokoro\voices-v1.0.bin`).
- Admin GPU Validation panel reports what wrappers expose; it cannot replace `nvidia-smi -l 1` process checks.

## Phase 22D local wrapper CORS cleanup
- Browser CORS behavior matters for AskChappy local runtime checks: AskChappy runs at `http://127.0.0.1:4173` while local wrapper services run at `http://127.0.0.1:8880` and `http://127.0.0.1:8890`.
- Wrappers now return local-only CORS headers by default for:
  - `http://127.0.0.1:4173`
  - `http://localhost:4173`
- Defaults avoid unrestricted wildcard origins; optional wrapper CLI `--allowed-origin` can be repeated or comma-separated for explicit local overrides.
- PowerShell `curl` can report HTTP 200 even when browser fetch is still blocked by CORS policy.
- After wrapper updates, restart Kokoro and faster-whisper service windows, then re-check browser readiness.
