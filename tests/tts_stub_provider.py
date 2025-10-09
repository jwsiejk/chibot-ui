class RaisingTTSProvider:
    """Test double that raises during synthesis."""

    name = "raising_tts_stub"

    def synth(self, text):
        raise RuntimeError("stub_tts_failure")
