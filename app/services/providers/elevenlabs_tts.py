# app/services/providers/elevenlabs_tts.py
import os, json, hashlib, time
from ..httputil import http_bytes
from ...obs.metrics import observe

class ElevenLabsTTS:
    def __init__(self):
        self.api_key = os.environ.get("ELEVENLABS_API_KEY") or ""
        self.voice_id = os.environ.get("ELEVENLABS_VOICE_ID") or "EXAVITQu4vr4xnSDxMaL"
        self.output_format = os.environ.get("ELEVEN_OUTPUT_FORMAT") or "mp3_44100_128"
        if not self.api_key:
            raise RuntimeError("ELEVENLABS_API_KEY is required")

    def synth(self, text: str, *, voice_id: str | None = None, format: str | None = None):
        vid = voice_id or self.voice_id
        fmt = format or self.output_format

        # Idempotency cache in DB memory
        from ...db import db
        key_src = f"{(text or '').strip()}|{vid}|{fmt}"
        kid = hashlib.sha256(key_src.encode('utf-8')).hexdigest()
        cache = db.memory.setdefault('tts_cache', {})
        ttl = float(os.environ.get('ELEVEN_CACHE_TTL','600'))
        hit = cache.get(kid)
        if hit and (time.time() - hit.get('t',0)) < ttl:
            return hit['audio'], hit['visemes']

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{vid}"
        payload = {
            "text": text or "",
            "model_id": os.environ.get("ELEVEN_MODEL_ID") or None,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.5},
            "output_format": fmt
        }
        # cleanup None
        if payload["model_id"] is None:
            del payload["model_id"]
        data = json.dumps(payload).encode("utf-8")
        audio_bytes = http_bytes(
            url, data=data,
            headers={"Content-Type":"application/json","xi-api-key": self.api_key},
            timeout=float(os.environ.get("ELEVEN_TIMEOUT","30")),
            retries=int(os.environ.get("ELEVEN_RETRIES","2")),
            breaker_key="eleven.tts",
            breaker_threshold=int(os.environ.get("ELEVEN_CB_THRESHOLD","3")),
            breaker_cooldown=float(os.environ.get("ELEVEN_CB_COOLDOWN","10"))
        )
        # Temporary simple viseme schedule (Phase 11 will replace)
        dur_ms = max(600, min(8000, len(text or "") * 40))
        step = max(80, int(dur_ms/12))
        vis = [{"t_ms": i*step, "v": "A"} for i in range(int(dur_ms/step))]

        cache[kid] = {"audio": audio_bytes, "visemes": vis, "t": time.time()}
        observe("vendor.eleven.tts_bytes", len(audio_bytes), {"voice": vid})
        return audio_bytes, vis
