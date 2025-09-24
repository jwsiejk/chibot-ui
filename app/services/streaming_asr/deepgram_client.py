from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import AsyncGenerator, Optional, Any

import websockets  # provided by uvicorn[standard]


# Test-mode and last-observed info for CI assertions
DG_TEST_MODE = os.getenv("DG_TEST_MODE", "").strip() == "1"
DG_LAST_URL: str | None = None
DG_LAST_CONFIG: dict | None = None

logger = logging.getLogger(__name__)


class _FakeWSForTests:
    def __init__(self):
        self.open = True
        self.sent = []
    async def send(self, data):
        self.sent.append(data)
    async def close(self):
        self.open = False
    def __aiter__(self):
        # No incoming provider frames in test mode
        async def _gen():
            if False:
                yield None
        return _gen()

# ------------------------- URL & Config Helpers -------------------------------

def _clip_text(txt: str, limit: int = 120) -> str:
    try:
        txt = txt or ""
        if len(txt) <= limit:
            return txt
        return txt[:limit] + "…"
    except Exception:
        return ""

def _dg_url(overrides: Optional[dict] = None) -> str:
    """Return the Deepgram listen URL with safe defaults.

    Audio transport parameters must be in the URL query for Deepgram's v1/listen.
    """
    base = os.getenv("DEEPGRAM_LISTEN_URL", "wss://api.deepgram.com/v1/listen")

    # Append defaults if not present (helps when proxies ignore Configure frame)
    if "encoding=" not in base:
        sep = "&" if "?" in base else "?"
        base = (
            base
            + sep
            + "encoding=opus&sample_rate=48000&channels=1"
            + "&interim_results=true&vad_events=true&smart_format=true&punctuate=true&utterance_end_ms=1200"
        )

    # Apply overrides into query string if present, and ensure model from env if set
    try:
        import urllib.parse as _p
        parts = _p.urlsplit(base)
        q = _p.parse_qsl(parts.query, keep_blank_values=True)
        qd = {k: v for k, v in q}

        def _fmt(v):
            if isinstance(v, bool):
                return "true" if v else "false"
            if isinstance(v, int):
                return str(int(v))
            return str(v)

        if overrides:
            for k in (
                "encoding",
                "sample_rate",
                "channels",
                "interim_results",
                "smart_format",
                "punctuate",
                "vad_events",
                "utterance_end_ms",
                "model",
                "language",
            ):
                if k in overrides and overrides[k] is not None:
                    qd[k] = _fmt(overrides[k])

        # If DG_MODEL env is set and no model present yet, add it
        _env_model = os.getenv("DG_MODEL")
        if _env_model and "model" not in qd:
            qd["model"] = _env_model

        # If a language env is provided (optional), prefer it if not set
        _env_lang = os.getenv("DEEPGRAM_LANG")
        if _env_lang and "language" not in qd:
            qd["language"] = _env_lang

        query = _p.urlencode(qd)
        base = _p.urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))
    except Exception:
        pass

    return base

def _auth_header() -> str:
    key = os.getenv("DEEPGRAM_API_KEY", "").strip()
    if not key:
        raise RuntimeError("DEEPGRAM_API_KEY is not set")
    return f"Token {key}"

def _initial_config(overrides: Optional[dict] = None) -> dict:
    """Build Configure payload with FEATURES ONLY (no audio/transport keys)."""
    interim = os.getenv("DG_ENABLE_PARTIALS", "true").lower() != "false"
    features = {
        "interim_results": interim,
        "smart_format": True,
        "punctuate": True,
        "vad_events": True,
        # Note: utterance_end_ms is typically a URL/query setting; avoid here to
        # keep Configure schema compliant ("features" or "processors" only).
    }

    # Allow simple boolean overrides at top-level OR nested under "features"
    try:
        if overrides:
            # Merge nested features if provided
            if isinstance(overrides.get("features"), dict):
                for k, v in overrides["features"].items():
                    features[k] = v

            # Support legacy boolean overrides at top level
            for k in ("interim_results", "smart_format", "punctuate", "vad_events"):
                if k in overrides and overrides[k] is not None:
                    features[k] = bool(overrides[k])
    except Exception:
        pass

    cfg: dict[str, Any] = {"type": "Configure", "features": features}

    # Pass-through processors if supplied
    try:
        if overrides and isinstance(overrides.get("processors"), dict):
            cfg["processors"] = overrides["processors"]
    except Exception:
        pass

    return cfg

# ------------------------------ Client ---------------------------------------

class DeepgramClient:
    """Minimal async wrapper for Deepgram streaming WS, with graceful end.

    Additions:
      • Drops the tiny first chunk (<64B) before sending (common capture preamble).
      • Sends {"type": "CloseStream"} and lingers briefly before closing.
      • Waits for a final transcript (bounded) so Deepgram doesn’t record 00:00:00.
      • Gates sending until Deepgram signals it's ready (listening/connected/etc).
    """

    def __init__(self, _cfg: Optional[dict] = None) -> None:
        self._cfg = _cfg or {}
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

        # New: open gate (avoid race: don't send until DG is ready)
        self._open_evt: asyncio.Event = asyncio.Event()
        self._asr_open_emitted: bool = False
        self._open_wait_s: float = float(os.getenv("DG_OPEN_WAIT_S", "3.0"))

    # -- helpers ---------------------------------------------------------------

    def _sid_for_log(self) -> str:
        try:
            for key in ("session_id", "sid"):
                if key in self._cfg and self._cfg[key]:
                    return str(self._cfg[key])
        except Exception:
            pass
        return "?"

    # -- lifecycle -------------------------------------------------------------

    async def connect(self) -> None:
        """Open DG WS and send a Configure(features=...) frame."""
        global DG_LAST_URL, DG_LAST_CONFIG
        if self._ws:
            return

        url = _dg_url(self._cfg)
        sid = self._sid_for_log()
        logger.info("Deepgram connect start sid=%s url=%s", sid, url)

        if DG_TEST_MODE:
            # No network — use a fake socket and record URL/config for tests
            self._ws = _FakeWSForTests()
            DG_LAST_URL = url
            DG_LAST_CONFIG = _initial_config(self._cfg)
            # Mark as open immediately in tests
            self._open_evt.set()
            await self._ev_queue.put({"type": "asr_open"})
            logger.info("Deepgram test-mode connect sid=%s", sid)
            return

        headers = [("Authorization", _auth_header())]
        try:
            self._ws = await websockets.connect(
                url,
                additional_headers=headers,
                max_size=None,
            )
        except TypeError:
            self._ws = await websockets.connect(
                url,
                extra_headers=headers,
                max_size=None,
            )

        # Send initial configuration with FEATURES ONLY
        DG_LAST_URL = url
        DG_LAST_CONFIG = _initial_config(self._cfg)
        await self._ws.send(json.dumps(DG_LAST_CONFIG))

        # Optimistically mark the socket as open so send() doesn't block when the
        # websocket reports ready immediately. We still rely on provider events
        # in _rx_loop to emit the diagnostic asr_open event once Deepgram
        # confirms it is listening.
        if self._ws and getattr(self._ws, "open", False) and not self._open_evt.is_set():
            logger.info("Deepgram open gate (optimistic) sid=%s", sid)
            self._open_evt.set()

        # Start receiver (will set the open gate on first listening/metadata)
        self._rx_task = asyncio.create_task(self._rx_loop())
        logger.info("Deepgram connect ok sid=%s", sid)

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
        sid = self._sid_for_log()
        logger.info(
            "Deepgram close start sid=%s wait_for_final=%s linger_ms=%s",
            sid,
            wait_for_final,
            linger_ms,
        )

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
        logger.info("Deepgram close complete sid=%s", sid)

    # -- sending ---------------------------------------------------------------

    async def send(self, chunk: bytes) -> None:
        """Send a binary audio frame to Deepgram (Opus/PCM), gated on open."""
        if not isinstance(chunk, (bytes, bytearray)):
            raise TypeError("chunk must be bytes")
        if not chunk:
            return

        sid = self._sid_for_log()

        # Wait (briefly) for DG to report "open" (listening/metadata)
        if not self._open_evt.is_set():
            try:
                await asyncio.wait_for(self._open_evt.wait(), timeout=self._open_wait_s)
            except asyncio.TimeoutError:
                logger.warning("Deepgram send gated but no open within timeout sid=%s", sid)
                raise RuntimeError("deepgram_not_connected")

        if not self._ws or not getattr(self._ws, "open", False):
            logger.warning("Deepgram send called without active socket sid=%s", sid)
            raise RuntimeError("deepgram_not_connected")

        # Drop the tiny preamble as first "frame" (common with some recorders)
        if not self._first_real_sent and len(chunk) < self._min_valid_bytes:
            logger.debug(
                "Deepgram drop small chunk sid=%s bytes=%s min_bytes=%s",
                sid,
                len(chunk),
                self._min_valid_bytes,
            )
            return

        self._first_real_sent = True
        await self._ws.send(chunk)
        self._last_chunk_ts = time.time()
        logger.debug("Deepgram sent chunk sid=%s bytes=%s", sid, len(chunk))

    # -- events API ------------------------------------------------------------

    async def events(self) -> AsyncGenerator[dict, None]:
        """Yield ASR events: asr_open, user_partial, user_final, asr_error, ..."""
        while True:
            ev = await self._ev_queue.get()
            yield ev

    # -- receiver --------------------------------------------------------------

    async def _rx_loop(self) -> None:
        """Consume Deepgram messages and push partial/final transcripts to the queue."""
        sid = self._sid_for_log()
        try:
            async for raw in self._ws:  # type: ignore
                # Expect JSON text frames; ignore non-JSON control frames.
                try:
                    msg = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
                except Exception:
                    continue

                evt_type = (msg.get("type") or "").lower()

                # Mark open on initial provider acknowledgements
                if evt_type in ("metadata", "listening", "connected", "ready"):
                    if not self._open_evt.is_set():
                        self._open_evt.set()
                    if not self._asr_open_emitted:
                        try:
                            await self._ev_queue.put({"type": "asr_open"})
                        except Exception:
                            pass
                        self._asr_open_emitted = True
                    # Nothing else to emit for these
                    continue

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

                    # Seeing a result also implies socket is functioning
                    if not self._open_evt.is_set():
                        self._open_evt.set()
                    if not self._asr_open_emitted:
                        try:
                            await self._ev_queue.put({"type": "asr_open"})
                        except Exception:
                            pass
                        self._asr_open_emitted = True

                    logger.debug(
                        "Deepgram transcript sid=%s is_final=%s chars=%s preview=%s",
                        sid,
                        is_final,
                        len(text),
                        _clip_text(text),
                    )
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

                elif evt_type in ("error", "close"):
                    logger.warning(
                        "Deepgram error event sid=%s evt_type=%s detail=%s",
                        sid,
                        evt_type,
                        _clip_text(str(msg), 200),
                    )
                    try:
                        await self._ev_queue.put({"type": "asr_error", "error": msg.get("error") or evt_type})
                    except Exception:
                        pass

                else:
                    # Unknown / unhandled event
                    logger.debug(
                        "Deepgram unhandled event sid=%s evt_type=%s",
                        sid,
                        evt_type or "unknown",
                    )
                    continue

        except asyncio.CancelledError:
            return
        except websockets.ConnectionClosed as e:
            # If we already had a result, this can be normal; otherwise surface it.
            logger.warning(
                "Deepgram websocket closed sid=%s code=%s reason=%s had_result=%s",
                sid,
                getattr(e, "code", None),
                getattr(e, "reason", ""),
                self._any_result,
            )
            if not self._any_result:
                try:
                    await self._ev_queue.put(
                        {"type": "asr_error", "error": f"recv_closed:{getattr(e, 'code', '')}:{getattr(e, 'reason', '')}"}
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.exception("Deepgram rx loop error sid=%s", sid)
            try:
                await self._ev_queue.put({"type": "asr_error", "error": f"rx:{e.__class__.__name__}"})
            except Exception:
                pass
