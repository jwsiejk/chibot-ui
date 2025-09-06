
# app/services/providers/elevenlabs_tts.py
import os, json, base64, urllib.request

class ElevenLabsTTS:
    def __init__(self):
        self.api_key = os.environ.get("ELEVENLABS_API_KEY") or ""
        self.voice_id = os.environ.get("ELEVENLABS_VOICE_ID") or "EXAVITQu4vr4xnSDxMaL"  # default demo voice id
        self.output_format = os.environ.get("ELEVEN_OUTPUT_FORMAT") or "mp3_44100_128"

    def synth(self, text: str, *, voice_id: str | None = None, format: str | None = None):
        vid = voice_id or self.voice_id
        fmt = format or self.output_format
        if not self.api_key:
            allow = os.environ.get("ALLOW_MOCK_PROVIDERS","false").lower() in ("1","true","yes")
            prod = (os.environ.get("APP_ENV","" ).lower() in ("prod","production") or os.environ.get("ENV","" ).lower() in ("prod","production"))
            if prod or not allow:
                raise RuntimeError("ELEVENLABS_API_KEY missing and mocks disallowed")
            return b"FAKE_MP3_DATA", [{"t_ms": i*120, "v": "A"} for i in range(5)]
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{vid}"
        payload = {
            "text": text,
            "model_id": os.environ.get("ELEVEN_MODEL_ID") or None,
            "voice_settings": None,
            "output_format": fmt
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type","application/json")
        req.add_header("xi-api-key", self.api_key)
        # For simplicity, request non-streaming; response body is audio bytes
        with urllib.request.urlopen(req, timeout=30) as resp:
            audio_bytes = resp.read()
        # Visemes: ElevenLabs' alignment APIs vary; generate simple schedule as fallback
        dur_ms = max(600, min(8000, len(text) * 40))
        step = max(80, int(dur_ms/12))
        vis = [{"t_ms": i*step, "v": "A"} for i in range(int(dur_ms/step))]
        return audio_bytes, vis
