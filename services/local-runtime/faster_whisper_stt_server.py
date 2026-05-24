import argparse
import os
import tempfile
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from faster_whisper import WhisperModel


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local faster-whisper STT HTTP wrapper")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8890)
    parser.add_argument("--model", default="base.en")
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--language", default="en")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type)
    except Exception as exc:
        if args.device == "cuda":
            raise SystemExit(
                "Failed to load faster-whisper with requested device 'cuda'. "
                "Verify CUDA runtime and GPU support. "
                f"Error: {exc}"
            ) from exc
        raise

    app = FastAPI(title="AskChappy faster-whisper Local Runtime")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "ready": True,
            "model": args.model,
            "device": args.device,
            "compute_type": args.compute_type,
            "language": args.language,
            "loaded": True,
        }

    @app.post("/v1/transcribe")
    async def transcribe(
        file: UploadFile = File(...),
        model_override: str | None = Form(default=None, alias="model"),
        language_override: str | None = Form(default=None, alias="language"),
    ) -> dict[str, str]:
        if file.size == 0:
            return {"text": ""}

        suffix = os.path.splitext(file.filename or "audio.wav")[1] or ".wav"
        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
                tmp_path = tmp_file.name
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    tmp_file.write(chunk)

            active_language = (language_override or args.language or "").strip() or None
            _ = model_override  # accepted for compatibility; fixed local model remains loaded from CLI args
            segments, _ = model.transcribe(tmp_path, language=active_language)
            text = " ".join(segment.text.strip() for segment in segments).strip()
            return {"text": text}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"transcription failed: {exc}") from exc
        finally:
            await file.close()
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
