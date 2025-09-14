# app/services/streaming_asr/deepgram_client.py
from __future__ import annotations
import asyncio, json, os, ssl
from typing import AsyncGenerator, Dict, Any, Optional

# We prefer websockets (present in your stack). Some envs differ on the kwarg
# name for handshake headers; we try both to avoid the uvloop extra_headers issue.
try:
    import websockets  # type: ignore
except Exception as e:  # pragma: no cover
    websockets = None

class DeepgramClient:
    """
    Production Deepgram live transcription client over WebSocket.
    - No mocks/stubs: fail fast if DEEPGRAM_API_KEY is missing.
    - Sends config on connect; streams binary WebM/Opus chunks (48 kHz).
    - Yields normalized events: {"type": "user_partial"|"user_final", "text": str}
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

        self._ws = None
        self._connected = False

    async def connect(self):
        if websockets is None:
            raise RuntimeError("websockets package not available")

        ssl_ctx = ssl.create_default_context()
        headers_list = [
            ("Authorization", f"Token {self.api_key}"),
            ("User-Agent", "AskChip/DeepgramWS"),
        ]
        # Deepgram requires a JSON config frame right after opening
        cfg = {
            "type": "config",
            "model": self.model,
            "language": self.language,
            "encoding": self.encoding,
            "sample_rate": self.sample_rate,
            "interim_results": self.interim_results,
            "smart_format": self.smart_format,
        }

        # Different websockets versions may accept 'extra_headers' or 'additional_headers'.
        try:
            self._ws = await websockets.connect(self.listen_url, extra_headers=headers_list, ssl=ssl_ctx)  # websockets<=11/12
        except TypeError:
            # Fallback: some builds expect 'additional_headers' or dict headers
            try:
                self._ws = await websockets.connect(self.listen_url, additional_headers=headers_list, ssl=ssl_ctx)  # websockets>=13
            except TypeError:
                # Last try: pass dict headers via 'extra_headers'
                self._ws = await websockets.connect(self.listen_url, extra_headers=dict(headers_list), ssl=ssl_ctx)

        self._connected = True
        await self._ws.send(json.dumps(cfg))

    async def close(self):
        try:
            if self._ws is not None:
                await self._ws.close()
        finally:
            self._ws = None
            self._connected = False

    async def send(self, chunk: bytes):
        if not self._connected or self._ws is None:
            raise RuntimeError("DeepgramClient.send called while not connected.")
        await self._ws.send(chunk)

    async def poll_events(self, timeout: float = 0.05) -> AsyncGenerator[Dict[str, Any], None]:
        if not self._connected or self._ws is None:
            return
        ws = self._ws
        while True:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout)
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
                    first = alts[0] or {}
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
