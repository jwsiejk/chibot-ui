
# app/services/providers/fake_stt.py
class FakeSTT:
    def transcribe(self, audio_bytes: bytes, *, language: str = "en") -> str:
        return "mock transcript"
