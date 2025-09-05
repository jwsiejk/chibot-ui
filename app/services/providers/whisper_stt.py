# app/services/providers/whisper_stt.py
import os, json, uuid
from ..httputil import http_bytes

class WhisperSTT:
    def __init__(self):
        self.api_key = os.environ.get("OPENAI_API_KEY") or ""
        self.model = os.environ.get("OPENAI_STT_MODEL") or "whisper-1"
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is required for WhisperSTT")

    def transcribe(self, audio_bytes: bytes, *, language: str = "en") -> str:
        boundary = "----WebKitFormBoundary" + uuid.uuid4().hex
        def part(name, filename, content_type, data):
            return (                f"--{boundary}\r\n"                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'                f"Content-Type: {content_type}\r\n\r\n"            ).encode("utf-8") + data + b"\r\n"
        body = b""
        body += part("file","audio.webm","audio/webm", audio_bytes or b"")
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n{self.model}\r\n").encode("utf-8")
        if language:
            body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"language\"\r\n\r\n{language}\r\n").encode("utf-8")
        body += f"--{boundary}--\r\n".encode("utf-8")

        raw = http_bytes(
            "https://api.openai.com/v1/audio/transcriptions",
            data=body,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": f"multipart/form-data; boundary={boundary}"},
            timeout=float(os.environ.get("OPENAI_STT_TIMEOUT","60")),
            retries=int(os.environ.get("OPENAI_STT_RETRIES","2")),
            breaker_key="openai.stt",
            breaker_threshold=int(os.environ.get("OPENAI_CB_THRESHOLD","3")),
            breaker_cooldown=float(os.environ.get("OPENAI_CB_COOLDOWN","10"))
        )
        out = json.loads(raw.decode("utf-8"))
        return (out.get("text") or "").strip() or "ok"
