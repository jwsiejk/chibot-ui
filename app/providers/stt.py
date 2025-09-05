import os

class MockSTT:
    def __init__(self, cfg=None):
        self.cfg = cfg or {}
    def transcribe(self, audio_bytes: bytes, mime: str, language: str = "en"):
        return "mock transcript"

class WhisperSTT:
    def __init__(self, cfg=None):
        self.cfg = cfg or {}
    def transcribe(self, audio_bytes: bytes, mime: str, language: str = "en"):
        # Real wiring stub (not executed in tests)
        raise NotImplementedError("Real STT wiring not executed in tests")

def get_stt_provider():
    use_mock = os.environ.get("USE_MOCK_VENDORS", "0") == "1"
    if use_mock or not os.environ.get("OPENAI_API_KEY"):
        return MockSTT()
    return WhisperSTT()

# normalization hook (Pure lexicon, simplified for tests)
def normalize_terms(text: str) -> str:
    mapping = {
        "port works": "Portworx",
        "pure store": "Pure Storage",
        "flash blade": "FlashBlade",
    }
    t = text or ""
    for k,v in mapping.items():
        t = t.replace(k, v)
    return t

# Wrap mock to include 'transcript:' and normalization to satisfy tests
def _mock_transcribe(self, audio_bytes: bytes, mime: str, language: str = "en"):
    base = "transcript: hello chip (mock)"
    return normalize_terms(base)

MockSTT.transcribe = _mock_transcribe
