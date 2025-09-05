
# app/services/providers/mock_stt.py
class MockSTT:
    def transcribe(self, audio_bytes: bytes, *, language: str = "en") -> str:
        return "mock transcript"
