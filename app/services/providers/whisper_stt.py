
# app/services/providers/whisper_stt.py
import os, urllib.request, json, uuid

class WhisperSTT:
    def __init__(self):
        self.api_key = os.environ.get("OPENAI_API_KEY") or ""
        self.model = os.environ.get("OPENAI_STT_MODEL") or "whisper-1"  # can be 'gpt-4o-mini-transcribe'

    def transcribe(self, audio_bytes: bytes, *, language: str = "en") -> str:
        if not self.api_key:
            allow = os.environ.get("ALLOW_MOCK_PROVIDERS","false").lower() in ("1","true","yes")
            prod = (os.environ.get("APP_ENV","" ).lower() in ("prod","production") or os.environ.get("ENV","" ).lower() in ("prod","production"))
            if prod or not allow:
                raise RuntimeError("OPENAI_API_KEY missing and mocks disallowed")
            return "transcription unavailable (missing OPENAI_API_KEY)"
        # multipart/form-data POST to OpenAI audio.transcriptions
        boundary = "----WebKitFormBoundary" + uuid.uuid4().hex
        def part(name, filename, content_type, data):
            return (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("utf-8") + data + b"\r\n"
        body = b""
        body += part("file", "audio.webm", "audio/webm", audio_bytes)
        body += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="model"\r\n\r\n{self.model}\r\n'
        ).encode("utf-8")
        if language:
            body += (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="language"\r\n\r\n{language}\r\n'
            ).encode("utf-8")
        body += f"--{boundary}--\r\n".encode("utf-8")

        req = urllib.request.Request("https://api.openai.com/v1/audio/transcriptions", data=body, method="POST")
        req.add_header("Authorization", f"Bearer {self.api_key}")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        with urllib.request.urlopen(req, timeout=60) as resp:
            out = json.loads(resp.read().decode("utf-8"))
        return (out.get("text") or "").strip() or "ok"
