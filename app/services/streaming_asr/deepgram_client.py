from __future__ import annotations

import asyncio
import json
import os
from typing import AsyncGenerator, Optional, Any

import websockets  # provided by uvicorn[standard]


def _dg_url() -> str:
    """Return the Deepgram listen URL with safe defaults."""
    base = os.getenv("DEEPGRAM_LISTEN_URL", "wss://api.deepgram.com/v1/listen")
    # append defaults if not present (helps when proxies ignore Configure frame)
    if "encoding=" not in base:
        sep = "&" if "?" in base else "?"
        base = base + sep + "encoding=opus&sample_rate=48000&channels=1&interim_results=true"
    return base


def _auth_header() -> str:
    key = os.getenv("DEEPGRAM_API_KEY", "").strip()
    if not key:
        raise RuntimeError("DEEPGRAM_API_KEY is not set")
    return f"Token {key}"


def _initial_config() -> dict:
    model = os.getenv("DG_MODEL")
    interim = os.getenv("DG_ENABLE_PARTIALS", "true").lower() != "false"
    cfg = {
        "type": "Configure",
        "encoding": "opus",
        "sample_rate": 48000,
        "interim_results": interim,
        "vad_events": False,
    }
    if model:
        cfg["model"] = model
    return cfg


class DeepgramClient:
    """Minimal async wrapper for Deepgram streaming WS."""

    def __init__(self, _cfg: Optional[dict] = None) -> None:
        self._ws = None  # type: ignore
        self._rx_task: Optional[asyncio.Task] = None
        self._ev_queue: asyncio.Queue = asyncio.Queue()
        self._closed = False

    async def connect(self) -> None:
        if self._ws:
            return
        # First try with list[tuple] headers (newer style)
        try:
            self._ws = await websockets.connect(
                _dg_url(),
                extra_headers=[("Authorization", _auth_header())],
            )
        except TypeError:
            # Fallback: some builds accept dict headers instead
            self._ws = await websockets.connect(
                _dg_url(),
                extra_headers={"Authorization": _auth_header()},
            )

        # Send initial configuration to enable partials.
        await self._ws.send(json.dumps(_initial_config()))
        # Start receiver
        self._rx_task = asyncio.create_task(self._rx_loop())
        await self._ev_queue.put({"type": "asr_open"})

    async def close(self) -> None:
        self._closed = True
        try:
            if self._ws and getattr(self._ws, "open", True):
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
                try:
                    msg = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
                except Exception:
                    continue
                t = (msg.get("type") or "").lower()
                if t in ("results", "transcript", "partialtranscript", "speech.update"):
                    text = ""
                    is_final = False
                    if "channel" in msg:
                        try:
                            alts = msg["channel"]["alternatives"]
                            if isinstance(alts, list) and alts:
                                text = (alts[0].get("transcript") or "").strip()
                        except Exception:
                            pass
                        is_final = bool(msg.get("is_final"))
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
                    continue
                elif t in ("error", "close"):
                    await self._ev_queue.put({"type": "asr_error", "error": t})
                else:
                    continue
        except asyncio.CancelledError:
            return
        except Exception as e:
            try:
                await self._ev_queue.put({"type": "asr_error", "error": f"rx:{e.__class__.__name__}"})
            except Exception:
                pass
