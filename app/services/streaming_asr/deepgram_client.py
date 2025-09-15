from __future__ import annotations

import asyncio
import os
from typing import Optional, AsyncGenerator

# Force the async client from websockets (avoid sync create_connection path)
try:
    from websockets.legacy.client import connect as ws_connect  # async
except Exception:
    from websockets.client import connect as ws_connect  # async


def _dg_url() -> str:
    """
    Build the Deepgram listen URL with explicit container/codec hints.
    We keep everything in the URL to avoid providers that ignore JSON config.
    """
    base = os.getenv("DEEPGRAM_LISTEN_URL", "wss://api.deepgram.com/v1/listen")
    parts = []
    # Explicit container + codec + format
    if "container=" not in base:
        parts.append("container=webm")
    if "encoding=" not in base:
        parts.append("encoding=opus")
    if "sample_rate=" not in base:
        parts.append("sample_rate=48000")
    if "channels=" not in base:
        parts.append("channels=1")
    if "interim_results=" not in base:
        parts.append("interim_results=true")
    if parts:
        sep = "&" if "?" in base else "?"
        base = base + sep + "&".join(parts)
    return base


def _auth_header() -> str:
    key = (os.getenv("DEEPGRAM_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("DEEPGRAM_API_KEY is not set")
    return f"Token {key}"


class DeepgramClient:
    """
    Minimal async wrapper for Deepgram's v1 listen WS.
    We do not send a JSON 'configure' frame — URL params carry all settings.
    """

    def __init__(self, _cfg: Optional[dict] = None) -> None:
        self._ws = None  # type: ignore
        self._rx_task: Optional[asyncio.Task] = None
        self._ev_queue: asyncio.Queue = asyncio.Queue()

    async def connect(self) -> None:
        if self._ws:
            return
        # Async client; extra_headers works here
        self._ws = await ws_connect(_dg_url(), extra_headers=[("Authorization", _auth_header())])
        # Start receiver after open
        self._rx_task = asyncio.create_task(self._rx_loop())
        await self._ev_queue.put({"type": "asr_open"})

    async def close(self) -> None:
        try:
            if self._ws and getattr(self._ws, "open", False):
                await self._ws.close()
        finally:
            self._ws = None
            if self._rx_task:
                try:
                    self._rx_task.cancel()
                finally:
                    self._rx_task = None

    async def send(self, chunk: bytes) -> None:
        if not self._ws or not getattr(self._ws, "open", False):
            raise RuntimeError("deepgram_not_connected")
        if not isinstance(chunk, (bytes, bytearray)):
            raise TypeError("chunk must be bytes")
        await self._ws.send(chunk)

    async def events(self) -> AsyncGenerator[dict, None]:
        while True:
            ev = await self._ev_queue.get()
            yield ev

    async def _rx_loop(self) -> None:
        try:
            async for raw in self._ws:  # type: ignore
                # Deepgram replies are JSON text frames; avoid strict schema — surface key types we care about.
                try:
                    import json
                    msg = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
                except Exception:
                    continue

                t = (msg.get("type") or "").lower()
                # Common payload: {"type":"Results","channel":{"alternatives":[{"transcript":"..."}]},"is_final":false}
                if t in ("results", "result", "transcript", "partialtranscript", "speech.update"):
                    text = ""
                    is_final = bool(msg.get("is_final"))
                    if "channel" in msg:
                        try:
                            alts = msg["channel"]["alternatives"]
                            if isinstance(alts, list) and alts:
                                text = (alts[0].get("transcript") or "").strip()
                        except Exception:
                            pass
                    if "transcript" in msg and not text:
                        text = (msg.get("transcript") or "").strip()
                    if "speech_final" in msg:
                        is_final = is_final or bool(msg.get("speech_final"))
                    if not text:
                        continue
                    await self._ev_queue.put(
                        {"type": "user_final" if is_final else "user_partial", "text": text}
                    )
                elif t in ("metadata", "listening", "connected", "ready"):
                    # informative but not textual — ignore
                    continue
                elif t in ("error", "close"):
                    await self._ev_queue.put({"type": "asr_error", "error": t})
                else:
                    # unknown control type; ignore quietly
                    continue
        except asyncio.CancelledError:
            return
        except Exception as e:
            try:
                await self._ev_queue.put({"type": "asr_error", "error": f"rx:{e.__class__.__name__}"})
            except Exception:
                pass
