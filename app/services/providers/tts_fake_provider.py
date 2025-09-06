
# app/services/providers/fake_tts.py
import base64

class FakeTTS:
    def synth(self, text: str, *, voice_id: str | None = None, format: str | None = None):
        audio_bytes = b"FAKE_MP3_DATA"
        visemes = [{"t_ms": i*120, "v": "A"} for i in range(5)]
        return audio_bytes, visemes
