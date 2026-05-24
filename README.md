# AskChappy

AskChappy is local-first production software for DDN partner enablement workflows.

## Core docs
- V1 specification: `docs/ASKCHAPPY_V1_SPEC.md`
- Implementation contracts: `docs/IMPLEMENTATION_CONTRACTS.md`
- Build playbook: `docs/BUILD_PLAYBOOK.md`
- Local-first run guide: `docs/LOCAL_FIRST_RUN_GUIDE.md`
- Local runtime operator guide: `docs/LOCAL_RUNTIME_OPERATOR_GUIDE.md`
- Current implementation status: `docs/CURRENT_IMPLEMENTATION_STATUS.md`
- Local-first release checklist: `docs/LOCAL_FIRST_RELEASE_CHECKLIST.md`
- Dependency review: `docs/DEPENDENCY_REVIEW.md`
- Phase 10 cloned voice blocker note: `docs/PHASE10_CLONED_VOICE_BLOCKER_NOTE.md`

## Local verification
```bash
npm test
npm run lint
npm run verify
```

## Local run
```bash
npm run start
```

## Local environment configuration
1. Copy `.env.example` to `.env.local`.
2. Edit `.env.local` for your machine-local runtime endpoints/models as needed.
3. Run `npm run start`.

Windows PowerShell startup:
```powershell
copy .env.example .env.local
# edit .env.local
npm run start
```


Default local runtime URL: `http://127.0.0.1:4173/chappy`.

Additional local runtime commands:
```bash
npm run build:local-runtime
npm run smoke:local-runtime
```

## Env-file loading behavior (Phase 20B cleanup)
- Vite now loads `.env.local`/`.env` via `loadEnv` in `vite.config.ts` and maps only the local runtime keys used by AskChappy runtime helpers (`OLLAMA_*`, `KOKORO_TTS_*`, `FASTER_WHISPER_*`) onto `process.env.*` compile-time constants.
- This keeps the existing runtime config helpers (`getOllamaConfig`, `getKokoroTtsConfig`, `getFasterWhisperConfig`) as the single config contract while ensuring `.env.local` overrides are actually applied at runtime.
- No cloud/OpenAI/provider API secrets are introduced, and `.env.local` remains gitignored.

## Terminology and route policy
- Deployment model: local-first, local production/local MVP.
- Retired `/demo*` and `/visual-session*` routes remain inactive historical routes.


## Phase 19 update
- Added local faster-whisper STT with browser microphone input.
- Voice input is appended as canonical transcript messages using `text` with `source: voice`.
- No separate voice transcript model was added.
- No cloud STT/speech SDK was added; no cloud fallback exists.
- No RAG/document ingestion/content grounding added; content grounding / DDN document ingestion / RAG is deferred for now.
- No cloned voice provider adapter was added.


## Phase 20A local runtime hardening
- Added local runtime readiness checks for Ollama runtime/model, Kokoro TTS, faster-whisper STT, browser microphone availability, standard voice default, and cloned voice optional/gated status.
- Readiness checks use local HTTP only and never append transcript messages.
- Session state transitions are hardened to recover to ready after STT/Ollama/TTS failures without fake transcript events.
- Content grounding / DDN document ingestion / RAG is deferred for now. Future content grounding work remains out of scope until explicitly re-prioritized.

## Windows local runtime startup scripts (Phase 21B)
- Committed operator scripts: `scripts/check-local-runtime.ps1`, `scripts/start-kokoro-tts.ps1`, `scripts/start-faster-whisper-stt.ps1`, and `scripts/start-local-runtime.ps1`.
- Keep model/audio assets outside the repo (example default: `C:\\AskChipAssets\\kokoro`).
- Copy `.env.example` to `.env.local` and set machine-local runtime commands: `KOKORO_TTS_RUN_COMMAND` and `FASTER_WHISPER_RUN_COMMAND`.
- Start/check sequence from repo root (use separate PowerShell windows for focused service starters):
  - In window 1: `.\scripts\start-kokoro-tts.ps1`
  - In window 2: `.\scripts\start-faster-whisper-stt.ps1`
  - In another window: `.\scripts\check-local-runtime.ps1`
  - Then launch AskChappy: `.\scripts\start-local-runtime.ps1`
- Manual GPU validation: `nvidia-smi -l 1`. AskChappy cannot directly inspect Windows GPU process usage without a native helper/agent. Prioritize Ollama and faster-whisper GPU checks first; Kokoro GPU is optional unless TTS latency is poor.

## Phase 21C local runtime venv/env cleanup
- `.venv-local-runtime` is a local-only Python virtual environment for local GPU TTS/STT dependencies and must never be committed.
- `.env.local` stays machine-local and gitignored; `.env.example` remains committed as a safe template.
- Configure machine-local runner command values in `.env.local`:
  - `KOKORO_TTS_RUN_COMMAND=...`
  - `FASTER_WHISPER_RUN_COMMAND=...`
- Installing Python packages alone is not sufficient: AskChappy still requires local HTTP services at `http://127.0.0.1:8880` (Kokoro TTS) and `http://127.0.0.1:8890` (faster-whisper STT).
- Keep local model/audio assets out of git (for example `C:\AskChipAssets\kokoro\kokoro-v1.0.onnx` and `C:\AskChipAssets\kokoro\voices-v1.0.bin`).

Create and prepare the venv from repo root:
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
