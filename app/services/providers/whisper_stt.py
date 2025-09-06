# app/services/providers/whisper_stt.py
import os, urllib.request, json, uuid

class WhisperSTT:
    def __init__(self):
        self.api_key = os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY missing")
        self.model = os.environ.get("OPENAI_STT_MODEL") or "whisper-1"  # can be 'gpt-4o-mini-transcribe'

    def transcribe(self, audio_bytes: bytes, *, language: str = "en") -> str:
        boundary = uuid.uuid4().hex
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="model"\r\n\r\n{self.model}\r\n'
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="audio.webm"\r\n'
            f"Content-Type: audio/webm\r\n\r\n"
        ).encode("utf-8") + audio_bytes + b"\r\n"
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