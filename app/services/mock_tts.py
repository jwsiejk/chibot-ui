import base64
def synth(text: str):
    audio_bytes = b"FAKE_MP3_DATA"
    audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
    return audio_b64, [{"t_ms": i*120, "v": "A"} for i in range(5)]
