# app/services/providers/deepgram_stt.py
import os, json, urllib.request, urllib.error
from typing import Optional

class DeepgramSTT:
    def __init__(self):
        self.api_key = os.environ.get("DEEPGRAM_API_KEY")
        if not self.api_key:
            raise RuntimeError("DEEPGRAM_API_KEY missing")
        # Optional tuning from env; safe defaults
        self.model = os.environ.get("DEEPGRAM_MODEL", "nova-2-general")
        self.base_url = os.environ.get("DEEPGRAM_LISTEN_HTTP", "https://api.deepgram.com/v1/listen")
        self.smart_format = os.environ.get("DEEPGRAM_SMART_FORMAT", "true")
        self.punctuate = os.environ.get("DEEPGRAM_PUNCTUATE", "true")

    def transcribe(self, audio_bytes: bytes, *, language: str = "en") -> str:
        if not audio_bytes:
            return ""
        params = f"?model={self.model}&language={language}&smart_format={self.smart_format}&punctuate={self.punctuate}"
        url = self.base_url + params
        req = urllib.request.Request(url, data=audio_bytes, method="POST")
        # We don't know the exact codec; let server infer
        req.add_header("Content-Type", "application/octet-stream")
        req.add_header("Authorization", f"Token {self.api_key}")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8", "ignore")
                data = json.loads(raw)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = (e.read() or b"").decode("utf-8", "ignore")
            except Exception:
                pass
            raise RuntimeError(f"Deepgram HTTP {e.code}: {detail[:500]}") from e
        except Exception as e:
            raise RuntimeError(f"Deepgram request failed: {e.__class__.__name__}: {e}") from e

        # Parse common Deepgram response shapes
        try:
            # v1: results.channels[0].alternatives[0].transcript
            res = data.get("results") or {}
            chans = res.get("channels") or []
            if chans:
                alts = (chans[0] or {}).get("alternatives") or []
                if alts and isinstance(alts[0], dict):
                    txt = (alts[0].get("transcript") or "").strip()
                    return txt
        except Exception:
            pass
        # Fallback: some responses may put transcript at top-level (rare)
        return (data.get("transcript") or "").strip()
