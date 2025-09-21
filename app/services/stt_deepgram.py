# app/services/stt_deepgram.py — Deepgram pre-recorded STT (production-safe)
from __future__ import annotations
import os, requests, json
from typing import Optional, Tuple

DG_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
DG_URL     = os.getenv("DEEPGRAM_PRERECORDED_URL", "https://api.deepgram.com/v1/listen")

def _headers(mime: str) -> dict:
    if not DG_API_KEY:
        raise RuntimeError("DEEPGRAM_API_KEY not set")
    return {
        "Authorization": f"Token {DG_API_KEY}",
        "Content-Type": mime or "audio/webm; codecs=opus",
        "Accept": "application/json",
    }

def transcribe_bytes(audio: bytes,
                     mime: str = "audio/webm; codecs=opus",
                     language: str = "en",
                     model: Optional[str] = None,
                     timeout_s: int = 45) -> Tuple[str, dict]:
    """
    Calls Deepgram pre-recorded API with an in-memory audio blob and returns (transcript, raw_json).
    """
    if not audio:
        return "", {}
    params = {
        "smart_format": "true",
        "language": language or "en",
    }
    if model:
        params["model"] = model  # e.g. "nova-2"
    resp = requests.post(DG_URL, params=params, headers=_headers(mime), data=audio, timeout=timeout_s)
    resp.raise_for_status()
    j = resp.json()
    # Typical path: results.channels[0].alternatives[0].transcript
    try:
        transcript = j["results"]["channels"][0]["alternatives"][0]["transcript"]
        return transcript or "", j
    except Exception:
        # Fall back / defensive parse
        return (j.get("results", {}) or {}).get("transcript", "") or "", j
