
from __future__ import annotations
import asyncio, json, os, ssl, time
from typing import AsyncGenerator, Dict, Any, Optional
import websockets

class DeepgramClient:
    """
    Production Deepgram live transcription client over WebSocket.
    - No mocks/stubs: will fail fast if DEEPGRAM_API_KEY is missing.
    - Sends config on connect; streams binary audio chunks (WebM/Opus 48k).
    - Yields simplified events: {"type": "user_partial"|"user_final", "text": str}
    """
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg or {}
        self.api_key = os.environ.get("DEEPGRAM_API_KEY")
        if not self.api_key:
            raise RuntimeError("DEEPGRAM_API_KEY is not set — no mock ASR provider is allowed.")
        dgc = (self.cfg.get("deepgram") if isinstance(self.cfg.get("deepgram"), dict) else {}) or {}
        self.listen_url: str = dgc.get("listen_url") or "wss://api.deepgram.com/v1/listen"
        self.model: str = dgc.get("model") or "nova-3"
        self.language: str = dgc.get("language") or "en"
        self.smart_format: bool = bool(dgc.get("smart_format", True))
        self.encoding: str = dgc.get("encoding") or "opus"
        self.sample_rate: int = int(dgc.get("sample_rate") or 48000)
        self.interim_results: bool = bool(dgc.get("interim_results", True))
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._connected = False

    async def connect(self):
        headers = [
            ("Authorization", f"Token {self.api_key}"),
            ("User-Agent", "AskChip/DeepgramWS"),
        ]
        # Connect
        self._ws = await websockets.connect(self.listen_url, extra_headers=headers, ssl=ssl.create_default_context())
        self._connected = True
        # Send config as the first text frame (Deepgram requirement)
        config = {
            "type": "config",
            "model": self.model,
            "language": self.language,
            "encoding": self.encoding,
            "sample_rate": self.sample_rate,
            "interim_results": self.interim_results,
            "smart_format": self.smart_format,
        }
        await self._ws.send(json.dumps(config))

    async def close(self):
        try:
            if self._ws:
                await self._ws.close()
        finally:
            self._connected = False
            self._ws = None

    async def send(self, chunk: bytes):
        if not self._connected or not self._ws:
            raise RuntimeError("DeepgramClient.send called while not connected.")
        # Audio chunk is expected to be WebM/Opus 48k produced by MediaRecorder
        await self._ws.send(chunk)

    async def poll_events(self, timeout: float = 0.05) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Poll the websocket for JSON events and normalize them.
        Yields {"type": "user_partial"|"user_final", "text": "..."}.
        """
        if not self._connected or not self._ws:
            return
        ws = self._ws
        while True:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                yield {"type": "noop"}  # allow caller to perform housekeeping
                continue
            except websockets.ConnectionClosed:
                break
            except Exception as e:
                # Surface upstream; caller can count provider_errors and circuit-break
                raise

            text = ""
            is_final = False
            try:
                data = json.loads(msg)
                # Deepgram "Results" style payloads
                # Typical shapes (robust parsing without external docs):
                # {"type":"Results","channel":{"alternatives":[{"transcript":"...","confidence":0.98}],"is_final":false}}
                # {"type":"Results","channel":{"alternatives":[{"transcript":"..."}],"is_final":true}}
                # Or compact variants {"channel":{...},"is_final":true}
                ch = data.get("channel") or {}
                alts = ch.get("alternatives") or data.get("alternatives") or []
                if alts and isinstance(alts, list):
                    first = alts[0] or {}
                    text = (first.get("transcript") or "").strip()
                is_final = bool(
                    data.get("is_final") or
                    ch.get("is_final") or
                    (alts and isinstance(alts[0], dict) and alts[0].get("final"))
                )
            except Exception:
                # If not JSON or unexpected structure, just ignore
                continue

            if not text:
                continue
            yield {"type": "user_final" if is_final else "user_partial", "text": text}
