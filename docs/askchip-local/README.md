# AskChip Local Run Guide

## Localhost defaults
- Frontend dev server: `http://127.0.0.1:5173`
- Backend API: `http://127.0.0.1:8000`
- Backend WebSocket host: `ws://127.0.0.1:8000`
- Local API development expects CORS middleware to allow `http://127.0.0.1:5173` and `http://localhost:5173`.

## Backend virtual environment (required)
Use a dedicated backend environment at `services/askchip-api/.venv` so runtime diagnostics reflect the exact backend dependency set.

### Bash
1. Create and activate a Python 3.11+ virtual environment at `services/askchip-api/.venv`.
2. Install API dependencies:
   ```bash
   pip install -e .[dev]
   ```

### Windows + NVIDIA (supported GPU TTS setup)
Run the setup script from the repo root:

```powershell
./scripts/setup-askchip-local-windows-nvidia.ps1
```

This script:
- creates/uses `services/askchip-api/.venv`
- installs backend deps with `pip install -e .[dev]`
- removes CPU `onnxruntime`
- installs `onnxruntime-gpu`
- verifies `CUDAExecutionProvider` availability

> Use this script as the repo-supported GPU TTS installation path. It is intentionally the single documented path for Windows/NVIDIA GPU ONNX Runtime setup.

Activate the environment before running the backend:

```powershell
.\services\askchip-api\.venv\Scripts\Activate.ps1
```

## Start local services
Install UI dependencies once in `apps/askchip-ui`:

```bash
npm install
```

From the repo root:

- Windows:
  ```powershell
  ./scripts/run-askchip-local.ps1
  ```
- Bash:
  ```bash
  ./scripts/run-askchip-local.sh
  ```

Both run scripts require `services/askchip-api/.venv` and use that interpreter directly.

## TTS runtime diagnostics (GPU/CPU honesty)
- `ASKCHIP_TTS_DEVICE` defaults to `auto`.
- `auto` selects CUDA only when ONNX Runtime reports `CUDAExecutionProvider`; otherwise it falls back to CPU.
- Runtime fallback details are reported through `/api/v1/config` and `/api/v1/readiness`.

### Verify ONNX Runtime providers
From the backend `.venv`:

```bash
python -c "import onnxruntime as ort; print(ort.get_available_providers())"
```

### Verify backend diagnostics

```bash
curl http://127.0.0.1:8000/api/v1/config
curl http://127.0.0.1:8000/api/v1/readiness
```

### GPU success criteria
- `/api/v1/config` shows `tts_device = cuda`
- `/api/v1/config` shows `tts_provider = CUDAExecutionProvider`

### Honest fallback criteria
- `/api/v1/config` shows `tts_device = cpu`
- `/api/v1/config` includes `tts_warning` and `tts_fallback_reason` explaining why CUDA was unavailable
