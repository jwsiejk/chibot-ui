# app/services/streaming_asr/deepgram_client.py
from __future__ import annotations

import json
import os
from typing import AsyncGenerator, Dict, Any, Optional

import httpx


class DeepgramClient:
    """
    Production Deepgram live transcription client over WebSocket (no SDK).
    - Fail fast if DEEPGRAM_API_KEY is missing.
    - Explicitly declares WebM/Opus @ 48 kHz so partials/finals are emitted.
    - Streams binary chunks; yields normalized partial/final events.
    """

    def __init__(self, cfg: Dict[str, Any] | None):
        cfg = cfg or {}
        dgc = cfg.get("deepgram") or {}
        self.api_key: str = os.environ.get("DEEPGRAM_API_KEY") or dgc.get("api_key") or ""
        if not self.api_key:
            raise RuntimeError("DEEPGRAM_API_KEY missing")

        self.model: str = dgc.get("model") or "nova-2"
        self.sample_rate: int = int(dgc.get("sample_rate") or 48000)
        self.channels: int = int(dgc.get("channels") or 1)
        self.interim_results: bool = bool(dgc.get("interim_results", True))
        self.smart_format: bool = bool(dgc.get("smart_format", True))
        self.punctuate: bool = bool(dgc.get("punctuate", True))

        base = (dgc.get("url") or "wss://api.deepgram.com/v1/listen").rstrip("/")
        # IMPORTANT: encoding/container/sample_rate/channels explicitly declared
        self.ws_url: str = (
            f"{base}"
            f"?encoding=opus"
            f"&container=webm"
            f"&sample_rate={self.sample_rate}"
            f"&channels={self.channels}"
            f"&model={self.model}"
            f"&interim_results={'true' if self.interim_results else 'false'}"
            f"&punctuate={'true' if self.punctuate else 'false'}"
            f"&smart_format={'true' if self.smart_format else 'false'}"
        )

        self._client: Optional[httpx.AsyncClient] = None
        self._ws: Optional[httpx._transports.websocket.AsyncWebSocketSession] = None
        self._connected: bool = False

    async def connect(self) -> None:
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Token {self.api_key}",
                "User-Agent": "AskChip/DeepgramWS",
            },
            timeout=None,
        )
        # httpx 0.27+ API
        self._ws = await self._client.ws_connect(self.ws_url)
        self._connected = True

    async def send_bytes(self, data: bytes) -> None:
        if not (self._connected and self._ws):
            return
        await self._ws.send_bytes(data)

    async def events(self) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Yields: {"type": "user_partial"|"user_final", "text": str}
        """
        if not self._ws:
            return
        while True:
            try:
                text = await self._ws.receive_text()
            except Exception:
                break

            try:
                msg = json.loads(text)
            except Exception:
                continue

            # Deepgram "Results" schema
            if (msg.get("type") or "").lower() != "results":
                continue
            ch = msg.get("channel") or {}
            alts = ch.get("alternatives") or []
            if not alts:
                continue

            transcript = alts[0].get("transcript") or ""
            if transcript == "":
                # Deepgram sometimes sends empty partials—ignore them.
                continue

            if msg.get("is_final"):
                yield {"type": "user_final", "text": transcript}
            else:
                yield {"type": "user_partial", "text": transcript}

        self._connected = False

    async def close(self) -> None:
        try:
            if self._ws:
                await self._ws.aclose()
        except Exception:
            pass
        try:
            if self._client:
                await self._client.aclose()
        except Exception:
            pass
        self._connected = False
