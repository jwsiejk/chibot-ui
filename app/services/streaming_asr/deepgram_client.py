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

        # Ensure some model is present; default to nova-2 if none provided
        if "model" not in qd:
            qd["model"] = os.getenv("DEEPGRAM_MODEL", "nova-2")

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
        # Keep URL-only keys (like utterance_end_ms) out of Configure
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

        # Graceful shutdown coordination
        self._final_event: asyncio.Event = asyncio.Event()
        self._any_result: bool = False

        # First-chunk guard & timing
        self._first_real_sent: bool = False
        self._min_valid_bytes: int = int(os.getenv("DG_MIN_VALID_BYTES", "64"))
        self._last_chunk_ts: float = 0.0

        # Tunables
        self._linger_ms: int = int(os.getenv("DG_LINGER_MS", "600"))
        self._final_wait_s: float = float(os.getenv("DG_FINAL_WAIT_S", "12"))

        # Open gate
        self._open_evt: asyncio.Event = asyncio.Event()
        self._asr_open_emitted: bool = False
        self._open_wait_s: float = float(os.getenv("DG_OPEN_WAIT_S", "3.0"))
        self._open_gate_warned: bool = False

        # Keepalive
        self._keepalive_task: Optional[asyncio.Task] = None
        self._keepalive_interval: float = float(os.getenv("DG_KEEPALIVE_INTERVAL_S", "5.0"))

    
    def is_open(self) -> bool:
        """Return True if the underlying websocket appears open."""
        try:
            return bool(self._ws) and bool(getattr(self._ws, "open", False))
        except Exception:
            return False
# -- helpers ---------------------------------------------------------------

    async def _signal_ready(self) -> None:
        if not self._open_evt.is_set():
            self._open_evt.set()
        if not self._asr_open_emitted:
            self._asr_open_emitted = True
            try:
                await self._ev_queue.put({"type": "asr_open"})
            except Exception:
                pass

    def _sid_for_log(self) -> str:
        try:
            for key in ("session_id", "sid"):
                if key in self._cfg and self._cfg[key]:
                    return str(self._cfg[key])
        except Exception:
            pass
        return "?"

    async def wait_socket_open(self, timeout: float = 1.5) -> bool:
        """Micro-wait until the underlying websocket's .open flag is True."""
        if self._ws and getattr(self._ws, "open", False):
            return True
        end = time.time() + timeout
        while time.time() < end:
            if self._ws and getattr(self._ws, "open", False):
                return True
            await asyncio.sleep(0.01)
        return False

    # -- lifecycle -------------------------------------------------------------

    async def connect(self) -> None:
        global DG_LAST_URL, DG_LAST_CONFIG
        if self._ws:
            return

        url = _dg_url(self._cfg)
        sid = self._sid_for_log()
        logger.info("Deepgram connect start sid=%s url=%s", sid, url)

        if DG_TEST_MODE:
            self._ws = _FakeWSForTests()
            DG_LAST_URL = url
            DG_LAST_CONFIG = _initial_config(self._cfg)
            self._open_evt.set()
            await self._signal_ready()
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

        # Micro-wait to ensure the underlying socket is actually open
        await self.wait_socket_open(timeout=0.25)

        DG_LAST_URL = url
        DG_LAST_CONFIG = _initial_config(self._cfg)
        await self._ws.send(json.dumps(DG_LAST_CONFIG))

        await self._signal_ready()
        self._rx_task = asyncio.create_task(self._rx_loop())

        if self._keepalive_interval > 0:
            self._keepalive_task = asyncio.create_task(self._keepalive_loop())

        logger.info("Deepgram connect ok sid=%s", sid)

    async def close(
        self,
        wait_for_final: bool = True,
        timeout: Optional[float] = None,
        linger_ms: Optional[int] = None,
    ) -> None:
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

        await self._stop_keepalive()

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

        try:
            if self._ws and getattr(self._ws, "open", False):
                await self._ws.send(json.dumps({"type": "CloseStream"}))
        except Exception:
            pass

        if wait_for_final:
            if timeout is None:
                timeout = self._final_wait_s
            try:
                await asyncio.wait_for(self._final_event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                try:
                    await self._ev_queue.put(
                        {"type": "asr_error", "error": f"final_timeout:{timeout}s"}
                    )
                except Exception:
                    pass

        try:
            if self._ws and getattr(self._ws, "open", True):
                await self._ws.close()
        finally:
            self._ws = None
            task = self._rx_task
            self._rx_task = None
            if task:
                try:
                    task.cancel()
                except Exception:
                    pass
        logger.info("Deepgram close complete sid=%s", sid)

    # -- sending ---------------------------------------------------------------

    async def send(self, chunk: bytes) -> None:
        if not isinstance(chunk, (bytes, bytearray)):
            raise TypeError("chunk must be bytes")
        if not chunk:
            return

        sid = self._sid_for_log()

        if not self._open_evt.is_set():
            try:
                await asyncio.wait_for(self._open_evt.wait(), timeout=self._open_wait_s)
            except asyncio.TimeoutError:
                if not self._open_gate_warned:
                    logger.warning("Deepgram send gated but no open within timeout sid=%s", sid)
                    self._open_gate_warned = True
                await self._signal_ready()

        # Gate on actual websocket open with a brief micro-wait
        await self.wait_socket_open(timeout=0.25)

        if not self._ws or not getattr(self._ws, "open", False):
            logger.warning("Deepgram send called without active socket sid=%s", sid)
            raise RuntimeError("deepgram_not_connected")

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
        while True:
            ev = await self._ev_queue.get()
            yield ev

    # -- receiver --------------------------------------------------------------

    async def _rx_loop(self) -> None:
        sid = self._sid_for_log()
        try:
            async for raw in self._ws:  # type: ignore
                try:
                    msg = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
                except Exception:
                    continue

                evt_type = (msg.get("type") or "").lower()

                if evt_type in ("metadata", "listening", "connected", "ready"):
                    await self._signal_ready()
                    continue

                if evt_type in ("results", "transcript", "partialtranscript", "speech.update"):
                    text = ""
                    is_final = False

                    channel = msg.get("channel") or {}
                    alts = channel.get("alternatives")
                    if isinstance(alts, list) and alts:
                        text = (alts[0].get("transcript") or "").strip()

                    is_final = bool(channel.get("is_final")) or bool(msg.get("is_final"))

                    if "transcript" in msg and not text:
                        text = (msg.get("transcript") or "").strip()

                    if msg.get("speech_final") is True:
                        is_final = True
                    if evt_type == "utteranceend":
                        is_final = True

                    if not text:
                        continue

                    await self._signal_ready()

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
                        continue

                elif evt_type in ("error", "close"):
                    logger.warning(
                        "Deepgram error event sid=%s evt_type=%s detail=%s",
                        sid,
                        evt_type,
                        _clip_text(str(msg), 200),
                    )
                    try:
                        await self._ev_queue.put(
                            {"type": "asr_error", "error": msg.get("error") or evt_type}
                        )
                    except Exception:
                        pass

                else:
                    logger.debug("Deepgram unhandled event sid=%s evt_type=%s", sid, evt_type or "unknown")
                    continue

        except asyncio.CancelledError:
            return
        except websockets.ConnectionClosed as e:
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

    # -- keepalive -------------------------------------------------------------

    async def _keepalive_loop(self) -> None:
        """Send KeepAlive frames immediately and then periodically."""
        sid = self._sid_for_log()
        try:
            while not self._closed:
                ws = self._ws
                if not ws or not getattr(ws, "open", False):
                    break

                try:
                    await ws.send(json.dumps({"type": "KeepAlive"}))
                    logger.debug("Deepgram keepalive sid=%s interval=%s", sid, self._keepalive_interval)
                except Exception as exc:
                    logger.warning("Deepgram keepalive failed sid=%s err=%s", sid, exc)
                    break

                try:
                    await asyncio.sleep(self._keepalive_interval)
                except asyncio.CancelledError:
                    return
        finally:
            self._keepalive_task = None

    async def _stop_keepalive(self) -> None:
        task = self._keepalive_task
        if not task:
            return
        self._keepalive_task = None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
