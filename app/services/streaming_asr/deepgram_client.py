# app/services/streaming_asr/deepgram_client.py
from __future__ import annotations
import asyncio, json, os
from typing import AsyncGenerator, Dict, Any, Optional
from urllib.parse import urlparse
import httpx

class DeepgramClient:
    """
    Production Deepgram live transcription client over WebSocket using httpx.
    - No mocks: fail fast if DEEPGRAM_API_KEY is missing.
    - Sends config on connect; streams binary WebM/Opus chunks (48 kHz).
    - Yields {"type": "user_partial"|"user_final", "text": str}
    """
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg or {}
        self.api_key = os.environ.get("DEEPGRAM_API_KEY")
        if not self.api_key:
            raise RuntimeError("DEEPGRAM_API_KEY is not set — no mock ASR provider is allowed.")

        listen = os.environ.get("DEEPGRAM_LISTEN_URL") or (self.cfg.get("deepgram") or {}).get("listen_url") or "wss://api.deepgram.com/v1/listen"
        p = urlparse(listen)
        if not p.scheme or not p.netloc:
            listen = "wss://api.deepgram.com/v1/listen"
        self.listen_url: str = listen

        dgc = (self.cfg.get("deepgram") if isinstance(self.cfg.get("deepgram"), dict) else {}) or {}
        self.model: str = dgc.get("model") or "nova-3"
        self.language: str = dgc.get("language") or "en"
        self.smart_format: bool = bool(dgc.get("smart_format", True))
        self.encoding: str = dgc.get("encoding") or "opus"
        self.sample_rate: int = int(dgc.get("sample_rate") or 48000)
        self.interim_results: bool = bool(dgc.get("interim_results", True))

        self._client: Optional[httpx.AsyncClient] = None
        self._ws: Optional[httpx._transports.websocket.AsyncWebSocketSession] = None
        self._connected = False

    async def connect(self):
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Token {self.api_key}",
                "User-Agent": "AskChip/DeepgramWS",
            },
            timeout=None,
        )
        self._ws = await self._client.ws_connect(self.listen_url)
        self._connected = True
        cfg = {
            "type": "config",
            "model": self.model,
            "language": self.language,
            "encoding": self.encoding,
            "sample_rate": self.sample_rate,
            "interim_results": self.interim_results,
            "smart_format": self.smart_format,
        }
        await self._ws.send_json(cfg)

    async def close(self):
        try:
            if self._ws is not None:
                await self._ws.aclose()
        finally:
            self._ws = None
            if self._client is not None:
                try: await self._client.aclose()
                finally: self._client = None
            self._connected = False

    async def send(self, chunk: bytes):
        if not self._connected or self._ws is None:
            raise RuntimeError("DeepgramClient.send called while not connected.")
        await self._ws.send_bytes(chunk)

    async def poll_events(self, timeout: float = 0.05) -> AsyncGenerator[Dict[str, Any], None]:
        if not self._connected or self._ws is None:
            return
        ws = self._ws
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive_text(), timeout)
            except asyncio.TimeoutError:
                yield {"type": "noop"}; continue
            except Exception:
                break

            text = ""
            is_final = False
            try:
                data = json.loads(msg)
                ch = data.get("channel") or {}
                alts = ch.get("alternatives") or data.get("alternatives") or []
                if alts and isinstance(alts, list):
                    first = (alts[0] or {})
                    text = (first.get("transcript") or "").strip()
                is_final = bool(
                    data.get("is_final") or
                    ch.get("is_final") or
                    (alts and isinstance(alts[0], dict) and alts[0].get("final"))
                )
            except Exception:
                continue

            if not text:
                continue
            yield {"type": "user_final" if is_final else "user_partial", "text": text}
