# app/services/providers/elevenlabs_tts.py
import os, json, hashlib, time, re
from ..httputil import http_bytes, http_json

# Optional: lightweight metrics (safe if module present)
try:
    from ...obs.metrics import observe
except Exception:
    def observe(*a, **k): pass

_VISEME_MAP = {
    # Very simple mapping of common phonemes to coarse viseme buckets
    "AA":"A","AE":"A","AH":"A","AO":"O","AW":"O","AY":"A",
    "B":"BMP","P":"BMP","M":"BMP",
    "CH":"CDG","JH":"CDG","SH":"CDG","ZH":"CDG",
    "D":"CDG","T":"CDG","S":"CDG","Z":"CDG",
    "EH":"E","ER":"E","EY":"E",
    "F":"FV","V":"FV",
    "G":"CDG","K":"CDG","NG":"CDG",
    "HH":"ETC","W":"ETC","Y":"ETC","L":"ETC","R":"ETC","TH":"ETC","DH":"ETC",
    "IH":"I","IY":"I",
    "OW":"O","OY":"O","UH":"U","UW":"U",
}
def _to_viseme(ph: str) -> str:
    ph = (ph or "").upper()
    return _VISEME_MAP.get(ph, "ETC")

def _bitrate_from_format(fmt: str) -> int:
    # e.g., mp3_44100_128 → 128 kbps
    m = re.search(r"_(\d{2,3})$", (fmt or "").strip())
    if m:
        return int(m.group(1)) * 1000
    # Default to 128kbps if unknown
    return 128000

class ElevenLabsTTS:
    def __init__(self):
        self.api_key = os.environ.get("ELEVENLABS_API_KEY") or ""
        self.voice_id = os.environ.get("ELEVENLABS_VOICE_ID") or "EXAVITQu4vr4xnSDxMaL"
        self.output_format = os.environ.get("ELEVEN_OUTPUT_FORMAT") or "mp3_44100_128"
        if not self.api_key:
            raise RuntimeError("ELEVENLABS_API_KEY is required")

    def _synthesize(self, text: str, vid: str, fmt: str) -> bytes:
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{vid}"
        payload = {
            "text": text or "",
            "model_id": os.environ.get("ELEVEN_MODEL_ID") or None,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.5},
            "output_format": fmt
        }
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
        observe("vendor.eleven.tts_bytes", len(audio_bytes), {"voice": vid})
        return audio_bytes

    def _alignment(self, text: str, vid: str) -> list[dict]:
        # Hypothetical alignment endpoint (defensive fallback below)
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{vid}/alignment"
        payload = {"text": text or ""}
        try:
            out = http_json(
                url, payload=payload,
                headers={"Content-Type":"application/json","xi-api-key": self.api_key},
                timeout=float(os.environ.get("ELEVEN_ALIGN_TIMEOUT","20")),
                retries=int(os.environ.get("ELEVEN_ALIGN_RETRIES","2")),
                breaker_key="eleven.align",
                breaker_threshold=int(os.environ.get("ELEVEN_CB_THRESHOLD","3")),
                breaker_cooldown=float(os.environ.get("ELEVEN_CB_COOLDOWN","10"))
            )
            phonemes = out.get("phonemes") or out.get("segments") or []
            vis = []
            for ph in phonemes:
                # support either {'start_ms':..,'phoneme':..} or {'t_ms':..,'p':..}
                t = int(round(float(ph.get("start_ms") or ph.get("t_ms") or 0)))
                p = (ph.get("phoneme") or ph.get("p") or "").upper()
                vis.append({"t_ms": t, "v": _to_viseme(p)})
            return vis
        except Exception:
            return []

    def _sanitize_visemes(self, vis: list[dict], audio_bytes: bytes, fmt: str) -> list[dict]:
        # Ensure monotonic times and scale to match audio duration (±2%)
        if not vis:
            # fallback: simple schedule at 12 steps across estimated duration
            bitrate = _bitrate_from_format(fmt)
            dur_ms = max(600, int((len(audio_bytes) * 8 / max(1, bitrate)) * 1000))
            step = max(80, dur_ms // 12)
            return [{"t_ms": i*step, "v": "A"} for i in range(max(1, dur_ms // step))]

        vis = sorted(vis, key=lambda x: x.get("t_ms", 0))
        # remove duplicates and enforce monotonic increasing
        out = []
        last = -1
        for item in vis:
            t = int(item.get("t_ms", 0))
            if t <= last:
                t = last + 1
            out.append({"t_ms": t, "v": item.get("v","ETC")})
            last = t

        # Duration sanity vs estimated from bitrate
        bitrate = _bitrate_from_format(fmt)
        est_ms = max(1, (len(audio_bytes) * 8 / max(1, bitrate)) * 1000.0)
        end_ms = out[-1]["t_ms"] if out else 0
        if end_ms <= 0:
            return out
        diff = abs(end_ms - est_ms) / est_ms
        if diff > 0.02:
            # scale times to fit estimated duration
            scale = est_ms / end_ms
            for item in out:
                item["t_ms"] = int(round(item["t_ms"] * scale))
        return out

    def synth(self, text: str, *, voice_id: str | None = None, format: str | None = None):
        vid = voice_id or self.voice_id
        fmt = format or self.output_format

        # Idempotency cache in module scope (per-process)
        key_src = f"{(text or '').strip()}|{vid}|{fmt}"
        kid = hashlib.sha256(key_src.encode('utf-8')).hexdigest()
        _cache = getattr(self.__class__, "_CACHE", {})
        if kid in _cache and (time.time() - _cache[kid]["t"]) < float(os.environ.get("ELEVEN_CACHE_TTL","600")):
            return _cache[kid]["audio"], _cache[kid]["visemes"]

        audio_bytes = self._synthesize(text or "", vid, fmt)
        vis = self._alignment(text or "", vid)
        vis = self._sanitize_visemes(vis, audio_bytes, fmt)

        # store
        self.__class__._CACHE = _cache
        _cache[kid] = {"audio": audio_bytes, "visemes": vis, "t": time.time()}
        return audio_bytes, vis
