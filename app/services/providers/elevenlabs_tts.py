# app/services/providers/elevenlabs_tts.py
import os, json, base64, urllib.request, urllib.error, time, hashlib
from typing import Tuple, List

# Simple in-memory cache shared across instances
_TTS_CACHE: dict[str, Tuple[bytes, List[dict]]] = {}

class ElevenLabsTTS:
    def __init__(self):
        self.api_key = os.environ.get("ELEVENLABS_API_KEY") or ""
        self.voice_id = os.environ.get("ELEVENLABS_VOICE_ID") or "EXAVITQu4vr4xnSDxMaL"
        self.output_format = os.environ.get("ELEVEN_OUTPUT_FORMAT") or "mp3_44100_128"
        self.max_retries = int(os.environ.get("TTS_RETRIES", "1"))
        self.backoff_base = float(os.environ.get("TTS_BACKOFF_BASE", "0.1"))

    def _key(self, text: str, voice_id: str, fmt: str) -> str:
        h = hashlib.sha1(f"{voice_id}|{fmt}|{text}".encode("utf-8")).hexdigest()
        return h

    def synth(self, text: str, *, voice_id: str | None = None, format: str | None = None):
        vid = voice_id or self.voice_id
        fmt = format or self.output_format
        if not text:
            # Empty input => empty audio + empty visemes
            return b"", []

        # Check idempotent cache
        key = self._key(text, vid, fmt)
        if key in _TTS_CACHE:
            return _TTS_CACHE[key]

        # Build request
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{vid}"
        payload = {"text": text, "voice_settings": {}, "output_format": fmt}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type","application/json")
        req.add_header("xi-api-key", self.api_key or "TEST")

        # Retry loop
        attempts = 0
        while True:
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    audio_bytes = resp.read()
                break
            except (urllib.error.URLError, OSError) as e:
                attempts += 1
                if attempts > self.max_retries:
                    raise
                time.sleep(self.backoff_base * (2 ** (attempts - 1)))

        # Generate a simple viseme schedule (fallback) sized to text length
        dur_ms = max(600, min(8000, len(text) * 40))
        step = max(80, int(dur_ms/12))
        vis = [{"t_ms": i*step, "v": "A"} for i in range(int(dur_ms/step))]

        _TTS_CACHE[key] = (audio_bytes, vis)
        return audio_bytes, vis