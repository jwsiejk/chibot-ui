from __future__ import annotations

import asyncio
import json
import os
from typing import AsyncGenerator, Optional

import websockets  # provided transitively by uvicorn[standard]


def _dg_url() -> str:
    base = os.getenv("DEEPGRAM_LISTEN_URL", "wss://api.deepgram.com/v1/listen")
    # Add sane defaults via querystring for providers that expect URL-config
    sep = "&" if "?" in base else "?"
    q = "encoding=opus&sample_rate=48000&channels=1&interim_results=true"
    return base + (sep + q if "encoding=" not in base else "")


def _auth_header() -> str:
    key = os.getenv("DEEPGRAM_API_KEY", "").strip()
    if not key:
        raise RuntimeError("DEEPGRAM_API_KEY is not set")
    # Deepgram supports either "Token {key}" or "Bearer {key}"
    return f"Token {key}"


def _initial_config() -> dict:
    """
    Minimal but sane defaults for WebM/Opus 48 kHz, interim partials on.
    You can extend via env if desired (e.g., DG_MODEL=nova-2-general).
    """
    model = os.getenv("DG_MODEL")  # optional
    enable_partials = os.getenv("DG_ENABLE_PARTIALS", "true").lower() != "false"
    out: dict = {
        "type": "Configure",
        "encoding": "opus",
        "sample_rate": 48000,
        "interim_results": enable_partials,
        "vad_events": False,
    }
    if model:
        out["model"] = model
    return out


class DeepgramClient:
    """
    Thin async wrapper over Deepgram's v1 streaming WS.

    Usage:
        dg = DeepgramClient()
        await dg.connect()
        async for ev in dg.events(): ...
        await dg.send(b"...binary audio chunk...")
        await dg.close()
    """

    def __init__(self, _cfg: Optional[dict] = None) -> None:
        # _cfg present for parity with other providers; not required here.
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._rx_task: Optional[asyncio.Task] = None
        self._ev_queue: asyncio.Queue = asyncio.Queue()
        self._closed: bool = False

    async def connect(self) -> None:
        if self._ws:
            return
        headers = {
            "Authorization": _auth_header(),
            "User-Agent": "AskChip-ASR/1.0",
            "Accept": "application/json",
            "Content-Type": "audio/webm; codecs=opus",
        }
        self._ws = await websockets.connect(_dg_url(), extra_headers=headers, ping_interval=20, ping_timeout=20)

        # Send initial config to enable interim results etc.
        cfg = _initial_config()
        await self._ws.send(json.dumps(cfg))

        # Kick off receiver
        self._rx_task = asyncio.create_task(self._rx_loop())
        # surface an "open" event to callers who want to reflect state
        await self._ev_queue.put({"type": "asr_open"})

    async def close(self) -> None:
        self._closed = True
        try:
            if self._ws and self._ws.open:
                await self._ws.close()
        finally:
            self._ws = None
            if self._rx_task:
                self._rx_task.cancel()
                self._rx_task = None

    async def send(self, chunk: bytes) -> None:
        if not self._ws or not self._ws.open:
            raise RuntimeError("deepgram_not_connected")
        if not isinstance(chunk, (bytes, bytearray)):
            raise TypeError("chunk must be bytes")
        await self._ws.send(chunk)

    async def events(self) -> AsyncGenerator[dict, None]:
        """
        Yields dict events with at least:
          - {"type": "user_partial", "text": "..."}
          - {"type": "user_final",   "text": "..."}
        Also surfaces:
          - {"type": "asr_open"}
          - {"type": "asr_error", "error": "..."}
        """
        while True:
            ev = await self._ev_queue.get()
            yield ev

    # -------------------- internals --------------------

    async def _rx_loop(self) -> None:
        try:
            assert self._ws is not None
            async for raw in self._ws:
                # Deepgram sends JSON result frames (and config acks). Coerce to dict.
                try:
                    msg = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
                except Exception:
                    # unexpected frame; ignore
                    continue

                # Normalize various DG shapes to our minimal events.
                # Common DG payload (v1 listen):
                # {"type":"Results","channel":{"alternatives":[{"transcript":"..."}]},"is_final":false}
                t = (msg.get("type") or "").lower()
                if t in ("results", "transcript", "partialtranscript", "speech.update"):
                    # Pull transcript text if available
                    text = ""
                    is_final = False

                    if "channel" in msg:
                        try:
                            alts = msg["channel"]["alternatives"]
                            if alts and isinstance(alts, list):
                                text = (alts[0].get("transcript") or "").strip()
                        except Exception:
                            pass
                        is_final = bool(msg.get("is_final"))

                    # Some variants:
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
                    # Non-text info; ignore.
                    continue
                elif t in ("error", "close"):
                    await self._ev_queue.put({"type": "asr_error", "error": t})
                else:
                    # Unknown control message; ignore quietly.
                    continue
        except asyncio.CancelledError:
            return
        except Exception as e:
            # Bubble a terminal error to consumers
            try:
                await self._ev_queue.put({"type": "asr_error", "error": f"rx:{e.__class__.__name__}"})
            except Exception:
                pass
