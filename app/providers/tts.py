import base64
from app.services.providers.elevenlabs_tts import ElevenLabsTTS

class _Wrapper:
    def __init__(self):
        self._p = ElevenLabsTTS()
    def synthesize_with_visemes(self, text: str):
        return self._p.synth(text)

def get_tts_provider():
    # Legacy shim: always return ElevenLabs provider wrapper
    return _Wrapper()