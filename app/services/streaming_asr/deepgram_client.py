from __future__ import annotations

import asyncio
import json
import os
import time
from typing import AsyncGenerator, Optional, Any

import websockets  # provided by uvicorn[standard]


# ------------------------- URL & Config Helpers -------------------------------

def _dg_url() -> str:
    """Return the Deepgram listen URL with safe defaults."""
    base = os.getenv("DEEPGRAM_LISTEN_URL", "wss://api.deepgram.com/v1/listen")
    # Append defaults if not present (helps when proxies ignore Configure frame)
    if "encoding=" not in base:
        sep = "&" if "?" in base else "?"
        base = (
            base
            + sep
            + "encoding=opus&sample_rate=48000&channels=1&interim_results=true&vad_events=true&smart_format=true&punctuate=true&utterance_end_ms=1200"
        )
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
        "smart_format": True,
        "punctuate": True,
        "vad_events": True,
        "utterance_end_ms": 1200,
    }
    if model:
        cfg["model"] = model
    return cfg


# ------------------------------ Client ---------------------------------------

class DeepgramClient:
    """Minimal async wrapper for Deepgram streaming WS, with graceful end.

    Additions:
      • Drops the tiny first chunk (<64B) before sending (common capture preamble).
      • Sends {"type": "CloseStream"} and lingers briefly before closing.
      • Waits for a final transcript (bounded) so Deepgram doesn’t record 00:00:00.
    """

    def __init__(self, _cfg: Optional[dict] = None) -> None:
        self._ws = None  # type: ignore
        self._rx_task: Optional[asyncio.Task] = None
        self._ev_queue: asyncio.Queue = asyncio.Queue()
        self._closed = False

        # New: graceful shutdown coordination
        self._final_event: asyncio.Event = asyncio.Event()
        self._any_result: bool = False

        # New: first-chunk guard & timing
        self._first_real_sent: bool = False
        self._min_valid_bytes: int = int(os.getenv("DG_MIN_VALID_BYTES", "64"))
        self._last_chunk_ts: float = 0.0

        # Tunables (env overrides allowed)
        self._linger_ms: int = int(os.getenv("DG_LINGER_MS", "600"))          # after last chunk
        self._final_wait_s: float = float(os.getenv("DG_FINAL_WAIT_S", "8"))  # wait for final

    # -- lifecycle -------------------------------------------------------------

    async def connect(self) -> None:
        if self._ws:
            return
        # Try tuple headers first; fall back to dict headers for older stacks.
        try:
            self._ws = await websockets.connect(
                _dg_url(),
                extra_headers=[("Authorization", _auth_header())],
                max_size=None,
            )
        except TypeError:
            self._ws = await websockets.connect(
                _dg_url(),
                extra_headers={"Authorization": _auth_header()},
                max_size=None,
            )

        # Send initial configuration to enable partials / VAD / formatting.
        await self._ws.send(json.dumps(_initial_config()))
        # Start receiver
        self._rx_task = asyncio.create_task(self._rx_loop())
        await self._ev_queue.put({"type": "asr_open"})

    async def close(
        self,
        wait_for_final: bool = True,
        timeout: Optional[float] = None,
        linger_ms: Optional[int] = None,
    ) -> None:
        """Gracefully close the stream.

        Steps:
          1) Optional linger after last chunk (default 600 ms).
          2) Send {"type":"CloseStream"}.
          3) Optionally wait for final (default 8 s).
          4) Close the websocket and cancel the rx loop.
        """
        if self._closed:
            return
        self._closed = True

        # 1) Linger a bit so VAD/segmentation can finalize
        if linger_ms is None:
            linger_ms = self._linger_ms
        if self._last_chunk_ts > 0 and linger_ms > 0:
            elapsed_ms = int((time.time() - self._last_chunk_ts) * 1000)
            delay_ms = max(0, linger_ms - elapsed_ms)
            if delay_ms > 0:
                try:
                    await asyncio.sleep(delay_ms / 1000.0)
                except asyncio.CancelledError:
                    pass

        # 2) Explicit end-of-stream sentinel
        try:
            if self._ws and getattr(self._ws, "open", False):
                await self._ws.send(json.dumps({"type": "CloseStream"}))
        except Exception:
            # Non-fatal; proceed with close
            pass

        # 3) Wait for a final (bounded)
        if wait_for_final:
            if timeout is None:
                timeout = self._final_wait_s
            try:
                await asyncio.wait_for(self._final_event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                # Surface for higher-level diagnostics if someone is listening
                try:
                    await self._ev_queue.put(
                        {"type": "asr_error", "error": f"final_timeout:{timeout}s"}
                    )
                except Exception:
                    pass

        # 4) Close websocket and stop rx loop
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

    # -- sending ---------------------------------------------------------------

    async def send(self, chunk: bytes) -> None:
        """Send a binary audio frame to Deepgram (Opus/PCM)."""
        if not self._ws or not getattr(self._ws, "open", False):
            raise RuntimeError("deepgram_not_connected")
        if not isinstance(chunk, (bytes, bytearray)):
            raise TypeError("chunk must be bytes")
        if not chunk:
            return

        # Drop the tiny preamble as first "frame" (common with some recorders)
        if not self._first_real_sent and len(chunk) < self._min_valid_bytes:
            # belt-and-suspenders: do not forward this
            return

        self._first_real_sent = True
        await self._ws.send(chunk)
        self._last_chunk_ts = time.time()

    # -- events API ------------------------------------------------------------

    async def events(self) -> AsyncGenerator[dict, None]:
        """Yield ASR events: asr_open, user_partial, user_final, asr_error, ..."""
        while True:
            ev = await self._ev_queue.get()
            yield ev

    # -- receiver --------------------------------------------------------------

    async def _rx_loop(self) -> None:
        """Consume Deepgram messages and push partial/final transcripts to the queue."""
        try:
            async for raw in self._ws:  # type: ignore
                # Expect JSON text frames; ignore non-JSON control frames.
                try:
                    msg = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
                except Exception:
                    continue

                evt_type = (msg.get("type") or "").lower()

                # Typical Deepgram schema: {"type":"Results","channel":{"alternatives":[...],"is_final":bool}}
                if evt_type in ("results", "transcript", "partialtranscript", "speech.update"):
                    text = ""
                    is_final = False

                    channel = msg.get("channel") or {}
                    alts = channel.get("alternatives")
                    if isinstance(alts, list) and alts:
                        text = (alts[0].get("transcript") or "").strip()

                    # Prefer channel.is_final; fall back to top-level speech_final
                    is_final = bool(channel.get("is_final")) or bool(msg.get("is_final"))

                    if "transcript" in msg and not text:
                        text = (msg.get("transcript") or "").strip()

                    # Some accounts emit explicit flags:
                    if msg.get("speech_final") is True:
                        is_final = True
                    if evt_type == "utteranceend":
                        is_final = True

                    if not text:
                        # Nothing meaningful; keep listening
                        continue

                    try:
                        await self._ev_queue.put(
                            {"type": "user_final" if is_final else "user_partial", "text": text}
                        )
                    except Exception:
                        pass

                    if is_final:
                        self._any_result = True
                        self._final_event.set()
                        # Do not break; allow more finals if multiple utterances are expected
                        continue

                elif evt_type in ("metadata", "listening", "connected", "ready"):
                    # Informational; ignore.
                    continue

                elif evt_type in ("error", "close"):
                    try:
                        await self._ev_queue.put({"type": "asr_error", "error": msg.get("error") or evt_type})
                    except Exception:
                        pass

                else:
                    # Unknown / unhandled event
                    continue

        except asyncio.CancelledError:
            return
        except websockets.ConnectionClosed as e:
            # If we already had a result, this can be normal; otherwise surface it.
            if not self._any_result:
                try:
                    await self._ev_queue.put(
                        {"type": "asr_error", "error": f"recv_closed:{getattr(e, 'code', '')}:{getattr(e, 'reason', '')}"}
                    )
                except Exception:
                    pass
        except Exception as e:
            try:
                await self._ev_queue.put({"type": "asr_error", "error": f"rx:{e.__class__.__name__}"})
            except Exception:
                pass
