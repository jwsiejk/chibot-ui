import argparse
import base64
import inspect
import io
import wave
from pathlib import Path
from typing import Any

import onnxruntime as ort
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


class TtsRequest(BaseModel):
    text: str
    voice: str = "af_sarah"
    format: str = "wav"

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
    parser = argparse.ArgumentParser(description="Local Kokoro ONNX TTS HTTP wrapper")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8880)
    parser.add_argument("--model", required=True)
    parser.add_argument("--voices", required=True)
    parser.add_argument("--provider", choices=["cuda", "cpu"], default="cpu")
    parser.add_argument("--allowed-origin", action="append", default=None)
    return parser.parse_args()


def make_health(
    ready: bool,
    selected_provider: str,
    available: list[str],
    using: str | None,
    error: str | None,
    provider_argument_supported: bool,
    provider_note: str | None,
) -> dict[str, Any]:
    cuda_available = any("cuda" in p.lower() for p in available)
    return {
        "ready": ready,
        "selected_provider": selected_provider,
        "available_providers": available,
        "using_provider": using,
        "provider_argument_supported": provider_argument_supported,
        "provider_note": provider_note,
        "cuda_available": cuda_available,
        "error": error,
    }


def encode_wav(samples: Any, sample_rate: int) -> bytes:
    pcm = samples
    if hasattr(samples, "tolist"):
        pcm = samples.tolist()
    pcm16 = bytearray()
    for sample in pcm:
        value = max(-1.0, min(1.0, float(sample)))
        int16 = int(value * 32767.0)
        pcm16.extend(int16.to_bytes(2, byteorder="little", signed=True))

    with io.BytesIO() as buffer:
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(bytes(pcm16))
        return buffer.getvalue()


def main() -> None:
    args = parse_args()
    model_path = Path(args.model)
    voices_path = Path(args.voices)
    if not model_path.exists():
        raise SystemExit(f"Model file not found: {model_path}")
    if not voices_path.exists():
        raise SystemExit(f"Voices file not found: {voices_path}")

    available_providers = ort.get_available_providers()
    provider_name = "CUDAExecutionProvider" if args.provider == "cuda" else "CPUExecutionProvider"
    if args.provider == "cuda" and provider_name not in available_providers:
        raise SystemExit(
            "Requested provider 'cuda' but CUDAExecutionProvider is unavailable in onnxruntime. "
            f"Available providers: {available_providers}"
        )

    from kokoro_onnx import Kokoro

    try:
        init_signature = inspect.signature(Kokoro.__init__)
        provider_argument_supported = "providers" in init_signature.parameters
    except (TypeError, ValueError):
        provider_argument_supported = False

    if provider_argument_supported:
        tts_engine = Kokoro(str(model_path), str(voices_path), providers=[provider_name])
        using_provider = provider_name
        provider_note = None
    else:
        tts_engine = Kokoro(str(model_path), str(voices_path))
        using_provider = "unknown"
        provider_note = (
            "kokoro-onnx constructor does not expose provider selection; CUDA availability is reported "
            "but active execution provider cannot be confirmed from this API."
        )

    app = FastAPI(title="AskChappy Kokoro Local Runtime")
    allowed_origins = parse_allowed_origins(args.allowed_origin)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.get("/health")
    @app.get("/v1/health")
    def health() -> dict[str, Any]:
        return make_health(
            True,
            args.provider,
            available_providers,
            using_provider,
            None,
            provider_argument_supported,
            provider_note,
        )

    @app.post("/v1/tts")
    def tts(payload: TtsRequest) -> dict[str, str]:
        text = payload.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail="text must not be empty")
        if payload.format.lower() != "wav":
            raise HTTPException(status_code=400, detail="only wav format is supported")

        try:
            samples, sample_rate = tts_engine.create(text=text, voice=payload.voice, speed=1.0, lang="en-us")
            wav_bytes = encode_wav(samples, sample_rate)
            return {"audio_base64": base64.b64encode(wav_bytes).decode("ascii"), "audio_format": "wav"}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"tts synthesis failed: {exc}") from exc

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
