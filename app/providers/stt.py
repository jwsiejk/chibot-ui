
import os

class MockSTT:
    def __init__(self, cfg=None):
        self.cfg = cfg or {}
    def transcribe(self, audio_bytes: bytes, mime: str, language: str = "en"):
        # Produce a simple mock transcript
        return "mock transcript"

class WhisperSTT:
    def __init__(self, cfg=None):
        self.cfg = cfg or {}
    def transcribe(self, audio_bytes: bytes, mime: str, language: str = "en"):
        # Real wiring stub (not executed in tests)
        
import io, os

api_key = os.environ.get("OPENAI_API_KEY")
if not api_key:
    raise RuntimeError("OPENAI_API_KEY is not set")

# Lazy import of openai to avoid dependency during tests
try:
    from openai import OpenAI
except Exception as e:
    raise RuntimeError("openai package not installed in this environment") from e

client = OpenAI(api_key=api_key)
# Whisper/Audio transcribe — note: exact API may differ depending on SDK version
# We accept raw audio bytes and mime; library expects file-like object
audio_file = io.BytesIO(audio_bytes)
audio_file.name = "audio.webm"
res = client.audio.transcriptions.create(
    model="whisper-1",
    file=audio_file,
    language=language or os.environ.get("OPENAI_STT_LANGUAGE", "en")
)
return res.text if hasattr(res, "text") else str(res)


def get_stt_provider():
    use_mock = os.environ.get("USE_MOCK_VENDORS", "0") == "1"
    if use_mock or not os.environ.get("OPENAI_API_KEY"):
        return MockSTT()
    return WhisperSTT()
