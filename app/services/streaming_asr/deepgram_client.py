# app/services/streaming_asr/deepgram_client.py
from __future__ import annotations

import json
import os
from typing import AsyncGenerator, Dict, Any, Optional

import websockets  # provided transitively by uvicorn[standard]
from websockets.exceptions import WebSocketException


class DeepgramClient:
    """
    Production Deepgram live transcription client (no SDK).
    - Uses 'websockets' for a stable WS client.
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

        self._ws: Optional[websockets.WebSocketClientProtocol] = None

    async def connect(self) -> None:
        try:
            self._ws = await websockets.connect(
                self.ws_url,
                extra_headers={
                    "Authorization": f"Token {self.api_key}",
                    "User-Agent": "AskChip/DeepgramWS",
                },
                max_size=None,  # let provider frames be large
            )
        except Exception as e:
            raise RuntimeError(f"deepgram_connect_failed: {e.__class__.__name__}")

    async def send_bytes(self, data: bytes) -> None:
        ws = self._ws
        if not ws:
            return
        try:
            await ws.send(data)  # binary
        except WebSocketException:
            # let caller close and reopen on next enqueue cycle
            pass

    async def events(self) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Yields: {"type": "user_partial"|"user_final", "text": str}
        """
        ws = self._ws
        if not ws:
            return
        try:
            async for text in ws:
                # Deepgram sends text frames with JSON "Results"
                if not isinstance(text, str):
                    continue
                try:
                    msg = json.loads(text)
                except Exception:
                    continue
                if (msg.get("type") or "").lower() != "results":
                    continue
                ch = msg.get("channel") or {}
                alts = ch.get("alternatives") or []
                if not alts:
                    continue

                transcript = alts[0].get("transcript") or ""
                if transcript == "":
                    # skip empty partials
                    continue

                if msg.get("is_final"):
                    yield {"type": "user_final", "text": transcript}
                else:
                    yield {"type": "user_partial", "text": transcript}
        except WebSocketException:
            # upstream closed or errored
            return

    async def close(self) -> None:
        try:
            if self._ws:
                await self._ws.close()
        except Exception:
            pass
        self._ws = None
