# app/services/providers/elevenlabs_tts.py
import os, json, urllib.request, urllib.error, time, hashlib
from typing import Tuple, List
from ...db import db

try:
    from ...admin_log import emit as _admin_emit
except Exception:  # pragma: no cover - optional diagnostic pipe
    _admin_emit = None

_TTS_CACHE: dict[str, Tuple[bytes, List[dict]]] = {}


def _admin_emit_safe(kind: str, **payload) -> None:
    if not callable(_admin_emit):
        return
    try:
        _admin_emit(kind, **payload)
    except Exception:  # pragma: no cover - diagnostics must not fail synth
        pass

class ElevenLabsTTS:
    def __init__(self):
        self.api_key = os.environ.get("ELEVENLABS_API_KEY")
        if not self.api_key:
            raise RuntimeError("ELEVENLABS_API_KEY missing")
        cfg = db.get_config()
        self.voice_id = cfg.get('tts_voice_id') or os.environ.get('ELEVENLABS_VOICE_ID') or "EXAVITQu4vr4xnSDxMaL"
        self.output_format = cfg.get('tts_output_format') or os.environ.get('ELEVEN_OUTPUT_FORMAT') or "opus_24000"
        self.model_id = cfg.get('tts_model_id') or os.environ.get('ELEVEN_MODEL_ID') or "eleven_multilingual_v2"
        self.max_retries = int(os.environ.get("TTS_RETRIES", "2"))
        self.backoff_base = float(os.environ.get("TTS_BACKOFF_BASE", "0.2"))

    def _cache_key(self, text: str, voice_id: str, fmt: str) -> str:
        return f"{voice_id}|{fmt}|{hash(text)}"

    def synth(self, text: str, *, voice_id: str | None = None, format: str | None = None) -> tuple[bytes, list[dict]]:
        vid = (voice_id or self.voice_id).strip()
        fmt = (format or self.output_format).strip()

        key = self._cache_key(text, vid, fmt)
        if key in _TTS_CACHE:
            return _TTS_CACHE[key]

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{vid}/stream?output_format={fmt}"
        payload = json.dumps({
            "text": text,
            "model_id": self.model_id,
            "optimize_streaming_latency": 0,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.8}
        }).encode("utf-8")

        headers = {
            "xi-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg" if fmt.startswith("mp3") else "application/octet-stream",
        }

        attempts = 0
        last_err = None
        while attempts <= self.max_retries:
            attempts += 1
            req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            try:
                start_ts = time.monotonic()
                first_chunk_ts = None
                last_chunk_ts = None
                chunks: list[bytes] = []
                with urllib.request.urlopen(req, timeout=30) as resp:
                    if resp.status != 200:
                        body = resp.read().decode("utf-8", "ignore")
                        raise urllib.error.HTTPError(url, resp.status, f"TTS HTTP {resp.status}: {body}", resp.headers, None)
                    ctype = resp.headers.get('Content-Type', '') or ''
                    self.last_mime = ctype.strip().lower() or None
                    while True:
                        part = resp.read(64 * 1024)
                        if not part:
                            break
                        now = time.monotonic()
                        if first_chunk_ts is None:
                            first_chunk_ts = now
                        last_chunk_ts = now
                        chunks.append(part)
                end_ts = time.monotonic()
                audio_bytes = b"".join(chunks)
                # Minimal synthetic viseme schedule by duration estimate (assume ~128kbps)
                bitrate_bps = 128000.0
                est_ms = int((len(audio_bytes) * 8 / bitrate_bps) * 1000.0) if audio_bytes else 0
                dur_ms = max(300, est_ms)
                N = 12
                times = [int(round(i * dur_ms / (N - 1))) for i in range(N)]
                for i in range(1, len(times)):
                    if times[i] <= times[i-1]:
                        times[i] = times[i-1] + 1
                vis = [{"t_ms": t, "v": "A"} for t in times]
                _TTS_CACHE[key] = (audio_bytes, vis)
                synth_ms = int(max(0.0, (end_ts - start_ts) * 1000))
                if first_chunk_ts is not None:
                    first_byte_ms = int(max(0.0, (first_chunk_ts - start_ts) * 1000))
                    last_byte_ms = int(max(0.0, (end_ts - first_chunk_ts) * 1000))
                else:
                    first_byte_ms = 0
                    last_byte_ms = synth_ms
                latency_payload = {
                    "voice_id": vid,
                    "format": fmt,
                    "attempt": attempts,
                    "synth_ms": synth_ms,
                    "first_byte_ms": first_byte_ms,
                    "last_byte_ms": last_byte_ms,
                }
                _admin_emit_safe("tts_latency", **latency_payload)
                return audio_bytes, vis
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    if getattr(e, "fp", None):
                        detail = (e.fp.read() or b"").decode("utf-8", "ignore")
                except Exception:
                    pass
                status = getattr(e, "code", None)
                retriable = attempts <= self.max_retries
                backoff_s = self.backoff_base * (2 ** (attempts - 1)) if retriable else 0.0
                body_hash = ""
                if detail:
                    try:
                        body_hash = hashlib.sha256(detail.encode("utf-8", "ignore")).hexdigest()
                    except Exception:
                        body_hash = ""
                error_payload = {
                    "status": status,
                    "retriable": bool(retriable),
                    "attempt": attempts,
                    "backoff_ms": int(backoff_s * 1000),
                    "body_hash": body_hash,
                    "voice_id": vid,
                }
                _admin_emit_safe("tts_error", **error_payload)
                if e.code == 401:
                    raise RuntimeError(
                        "ElevenLabs returned 401 Unauthorized. Check ELEVENLABS_API_KEY, voice permissions, and project access. "
                        f"Endpoint={url}, Response={detail[:300]}"
                    ) from e
                last_err = e
                if not retriable:
                    break
            except Exception as e:
                retriable = attempts <= self.max_retries
                backoff_s = self.backoff_base * (2 ** (attempts - 1)) if retriable else 0.0
                body_hash = ""
                try:
                    body_hash = hashlib.sha256(str(e).encode("utf-8", "ignore")).hexdigest()
                except Exception:
                    body_hash = ""
                error_payload = {
                    "status": getattr(e, "code", None),
                    "retriable": bool(retriable),
                    "attempt": attempts,
                    "backoff_ms": int(backoff_s * 1000),
                    "body_hash": body_hash,
                    "voice_id": vid,
                    "error": e.__class__.__name__,
                }
                _admin_emit_safe("tts_error", **error_payload)
                last_err = e
                if not retriable:
                    break
            if attempts <= self.max_retries:
                time.sleep(self.backoff_base * (2 ** (attempts - 1)))
        raise RuntimeError(f"TTS synth failed after {self.max_retries} attempts: {last_err}")

    def get_last_mime(self):
        return getattr(self, 'last_mime', None)
