import argparse
import logging
import os
import tempfile
import traceback
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from faster_whisper import WhisperModel

logger = logging.getLogger("faster_whisper_stt_server")


def parse_allowed_origins(values: list[str] | None) -> list[str]:
    if not values:
        return ["http://127.0.0.1:4173", "http://localhost:4173"]

    origins: list[str] = []
    for item in values:
        for origin in item.split(","):
            clean = origin.strip()
            if clean:
                origins.append(clean)

    return origins or ["http://127.0.0.1:4173", "http://localhost:4173"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local faster-whisper STT HTTP wrapper")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8890)
    parser.add_argument("--model", default="base.en")
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--language", default="en")
    parser.add_argument("--allowed-origin", action="append", default=None)
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
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
    allowed_origins = parse_allowed_origins(args.allowed_origin)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

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
        if file.filename is None:
            raise HTTPException(status_code=400, detail="missing uploaded filename")

        if file.size == 0:
            return {"text": ""}

        suffix = os.path.splitext(file.filename or "audio.wav")[1] or ".wav"
        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
                tmp_path = tmp_file.name
                total_bytes = 0
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    tmp_file.write(chunk)
            if total_bytes == 0:
                return {"text": ""}

            active_language = (language_override or args.language or "").strip() or None
            _ = model_override  # accepted for compatibility; fixed local model remains loaded from CLI args
            segments, _ = model.transcribe(tmp_path, language=active_language)
            text = " ".join(segment.text.strip() for segment in segments).strip()
            return {"text": text}
        except Exception as exc:
            logger.error(
                "STT transcription failed for uploaded audio: filename=%r content_type=%r suffix=%r error=%s\n%s",
                file.filename,
                file.content_type,
                suffix,
                str(exc),
                traceback.format_exc(),
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "transcription_failed",
                    "detail": str(exc),
                    "filename": file.filename,
                    "content_type": file.content_type,
                    "suffix": suffix,
                },
            ) from exc
        finally:
            await file.close()
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
