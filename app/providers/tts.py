import os, base64

class MockTTS:
    def __init__(self, cfg=None):
        self.cfg = cfg or {}
    def synthesize_with_visemes(self, text: str):
        audio_bytes = b"MOCKAUDIO"
        visemes = [{"t_ms": i*120, "v": v} for i, v in enumerate(["A","B","C","D","E"])]
        return audio_bytes, visemes

class ElevenLabsTTS:
    def __init__(self, cfg=None):
        self.cfg = cfg or {}
    def synthesize_with_visemes(self, text: str):
        # Real wiring stub (not used in tests)
        # You can enable this with env+requests in production if desired.
        # For now, return a deterministic fake similar to Mock to keep UI moving.
        audio_bytes = b"ELEVEN_MOCK"
        tokens = text.split() or ["hi"]
        visemes = []
        t = 0
        for tok in tokens:
            v = "ABCDE"[len(tok) % 5]
            visemes.append({"t_ms": t, "v": v})
            t += max(80, min(220, 20*len(tok)))
        return audio_bytes, visemes

def get_tts_provider():
    use_mock = os.environ.get("USE_MOCK_VENDORS", "0") == "1"
    if use_mock or not os.environ.get("ELEVENLABS_API_KEY"):
        return MockTTS()
    return ElevenLabsTTS()