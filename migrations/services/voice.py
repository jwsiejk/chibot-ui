# services/voice.py
# Placeholders that call your vendors (Whisper/Deepgram/etc., ElevenLabs/Polly/etc.)

def transcribe_audio(file_bytes: bytes, mime: str, filename: str | None = None) -> str:
    # TODO: call your STT provider and return the transcript string
    return ""

def synthesize_audio(text: str) -> str:
    # TODO: call your TTS provider and return a URL/path to the audio
    return None
