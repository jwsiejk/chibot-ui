from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import AsyncGenerator, Optional, Any, Deque
from collections import deque

import websockets  # provided by uvicorn[standard]


# Test-mode and last-observed info for CI assertions
DG_TEST_MODE = os.getenv("DG_TEST_MODE", "").strip() == "1"
DG_LAST_URL: str | None = None
DG_LAST_CONFIG: dict | None = None

_TAG_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_.:-]")


def _sanitize_tag(val: Optional[str], *, limit: int = 64) -> Optional[str]:
    if not val:
        return None
    try:
        txt = str(val)
    except Exception:
        return None
    txt = _TAG_SANITIZE_RE.sub("_", txt)
    if not txt:
        return None
    return txt[:limit]

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
    For **containerized Opus** (OGG/WebM), we must NOT send encoding, sample_rate, or channels.
    """
    base = os.getenv("DEEPGRAM_LISTEN_URL", "wss://api.deepgram.com/v1/listen")

    # Detect containerized Opus from overrides (nested under _transport)
    containerized = False
    try:
        if overrides and isinstance(overrides.get("_transport"), dict):
            containerized = bool(overrides["_transport"].get("containerized_opus"))
    except Exception:
        containerized = False

    # Append conservative defaults ONLY when not containerized
    if (not containerized) and ("encoding=" not in base):
        sep = "&" if "?" in base else "?"
        base = (
            base
            + sep
            # RAW defaults; safe for legacy raw paths. If truly containerized, these will be stripped below.
            + "encoding=opus&sample_rate=48000&channels=1"
            + "&interim_results=true&vad_events=true&smart_format=true&punctuate=true&utterance_end_ms=1200"
        )

    # Apply overrides into query string and clean up for containerized
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

        tag_source: Optional[str] = None

        # Allow top-level overrides (model, language, etc.)
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

            for key in ("_url_tag", "dg_url_tag", "url_tag"):
                try:
                    if overrides.get(key):
                        tag_source = str(overrides[key])
                        break
                except Exception:
                    continue
            if not tag_source:
                try:
                    sid_val = overrides.get("session_id") or overrides.get("sid")
                    if sid_val:
                        tag_source = f"sid:{sid_val}"
                except Exception:
                    pass

        env_tag = os.getenv("DG_URL_TAG", "").strip() or None
        tag = tag_source or env_tag
        if tag_source and env_tag:
            tag = f"{env_tag}:{tag_source}"
        safe_tag = _sanitize_tag(tag)
        if safe_tag:
            qd["tag"] = safe_tag

        # If containerized, remove transport params regardless of how they got here
        if containerized:
            for k in ("encoding", "sample_rate", "channels"):
                if k in qd:
                    qd.pop(k, None)
        else:
            # Non-containerized path: allow env overrides for raw parameters (no behavior change if unset)
            enc = os.getenv("DG_RAW_ENCODING")
            sr = os.getenv("DG_RAW_SAMPLE_RATE")
            ch = os.getenv("DG_RAW_CHANNELS")
            if enc:
                qd["encoding"] = enc
            if sr:
                qd["sample_rate"] = sr
            if ch:
                qd["channels"] = ch

        # If DG_MODEL env is set and no model present yet, add it (back-compat)
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


def _diagnostic_config(payload: dict, overrides: Optional[dict] = None) -> dict:
    diag = dict(payload)
    try:
        if overrides:
            for key in (
                "encoding",
                "sample_rate",
                "channels",
                "language",
                "model",
                "utterance_end_ms",
                "interim_results",
                "smart_format",
                "punctuate",
                "vad_events",
            ):
                if overrides.get(key) is not None:
                    diag[key] = overrides[key]
    except Exception:
        pass
    return diag

# ------------------------------ Client ---------------------------------------

class DeepgramClient:
    """Async wrapper for Deepgram streaming WS with internal send queue.

    Improvements:
      • Suppresses raw audio params for containerized Opus (WebM/OGG).
      • Logs a concise 'asr_url' diagnostic with omitted/raw params for verification.
      • Maintains an internal TX queue — early chunks are queued and flushed on open.
      • Drops tiny preamble chunk (<DG_MIN_VALID_BYTES) once, to avoid bogus data.
      • Sends {"type": "CloseStream"} and lingers briefly before close.
      • Waits for a final transcript (bounded) so Deepgram doesn’t record 00:00:00.
    """

    def __init__(self, _cfg: Optional[dict] = None) -> None:
        self._cfg = _cfg or {}
        self._ws = None  # type: ignore
        self._rx_task: Optional[asyncio.Task] = None
        self._ev_queue: asyncio.Queue = asyncio.Queue()
        self._closed = False
        self._closing = False
        self._url_tag: Optional[str] = None

        # TX queue + flushing
        self._tx_queue: Deque[bytes] = deque()
        self._flush_task: Optional[asyncio.Task] = None

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

        # Optional JSON logger injected by WS layer (ws_asgi)
        self._jlog = self._cfg.get("_jlog")

        # Serialize outbound websocket sends to avoid concurrent writer errors
        self._send_lock = asyncio.Lock()

    def is_open(self) -> bool:
        """Return True if the underlying websocket appears open."""
        try:
            return bool(self._ws) and bool(getattr(self._ws, "open", False))
        except Exception:
            return False

    def _had_result(self) -> bool:
        """True if any final result has been observed (event-aware)."""
        if self._any_result:
            return True
        if self._final_event.is_set():
            self._any_result = True
            return True
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
        # schedule a flush shortly after ASR open (lets DG finish configure)
        self._schedule_flush(delay=0.05)

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

    def _schedule_flush(self, delay: float = 0.0) -> None:
        if self._flush_task and not self._flush_task.done():
            return
        async def _runner():
            if delay > 0:
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    return
            try:
                await self._flush_tx()
            except Exception:
                logger.debug("Deepgram flush_tx raised; will retry on next trigger", exc_info=True)
        self._flush_task = asyncio.create_task(_runner())

    async def _flush_tx(self) -> None:
        """Drain queued audio if the socket is open and we've signaled ready."""
        if not self._tx_queue:
            return
        # Wait until socket open — don't raise; just give it a short chance
        await self.wait_socket_open(timeout=float(os.getenv('DG_OPEN_MICRO_WAIT_S', '0.75')))
        if not self._ws or not getattr(self._ws, "open", False):
            return
        # Ensure we've passed the ready/open gate
        if not self._open_evt.is_set():
            try:
                await asyncio.wait_for(self._open_evt.wait(), timeout=self._open_wait_s)
            except asyncio.TimeoutError:
                pass

        transport_cfg = {}
        try:
            transport_cfg = (self._cfg or {}).get("_transport") or {}
        except Exception:
            transport_cfg = {}
        containerized = bool(transport_cfg.get("containerized_opus"))

        sid = self._sid_for_log()
        while self._tx_queue and self._ws and getattr(self._ws, "open", False):
            data = self._tx_queue[0]
            # Drop tiny preamble once
            if (
                not containerized
                and (not self._first_real_sent)
                and len(data) < self._min_valid_bytes
            ):
                logger.debug(
                    "Deepgram drop small queued chunk sid=%s bytes=%s min_bytes=%s",
                    sid, len(data), self._min_valid_bytes
                )
                self._tx_queue.popleft()
                continue
            try:
                async with self._send_lock:
                    await self._ws.send(data)
                self._first_real_sent = True
                self._last_chunk_ts = time.time()
                self._tx_queue.popleft()
                logger.debug("Deepgram sent chunk (flush) sid=%s bytes=%s queued=%s",
                             sid, len(data), len(self._tx_queue))
                if callable(self._jlog):
                    try:
                        self._jlog(
                            "dg_forward",
                            sid=sid,
                            tag=self._url_tag,
                            bytes=len(data),
                            queued=len(self._tx_queue),
                        )
                    except Exception:
                        pass
            except Exception as e:
                # Transient send issue; stop and retry on next trigger
                logger.debug("Deepgram deferred send sid=%s err=%s queued=%s",
                             sid, type(e).__name__, len(self._tx_queue))
                break

    # -- lifecycle -------------------------------------------------------------

    async def connect(self) -> None:
        global DG_LAST_URL, DG_LAST_CONFIG
        if self._ws:
            return

        url = _dg_url(self._cfg)
        sid = self._sid_for_log()

        containerized = False

        # Diagnostic: parse params and emit compact JSON log that shows
        # whether we omitted encoding/sample_rate/channels (containerized) or sent raw.
        try:
            import urllib.parse as _p
            q = dict(_p.parse_qsl(_p.urlsplit(url).query, keep_blank_values=True))
            self._url_tag = q.get("tag") or None
            transport = (self._cfg or {}).get("_transport", {}) or {}
            containerized = bool(transport.get("containerized_opus"))
            url_meta = {
                "container": transport.get("container"),
                "codec": transport.get("codec"),
                "containerized_opus": containerized,
                "omitted_params": None,
                "raw_params": None,
            }
            if containerized:
                # These should be absent in containerized mode
                omitted = []
                for k in ("encoding", "sample_rate", "channels"):
                    if k not in q:
                        omitted.append(k)
                url_meta["omitted_params"] = omitted
            else:
                url_meta["raw_params"] = {
                    "encoding": q.get("encoding"),
                    "sample_rate": q.get("sample_rate"),
                    "channels": q.get("channels"),
                }

            # Structured JSON log if _jlog is available (preferred for your admin viewer)
            if callable(self._jlog):
                try:
                    self._jlog(
                        "asr_url",
                        url=url,
                        container=url_meta.get("container"),
                        codec=url_meta.get("codec"),
                        containerized_opus=bool(url_meta.get("containerized_opus")),
                        omitted_params=url_meta.get("omitted_params"),
                        raw_params=url_meta.get("raw_params"),
                    )
                except Exception:
                    pass

            # Keep existing human-readable info log
            logger.info(
                "dg_ws_connect sid=%s url=%s containerized_opus=%s sent_encoding=%s sent_sample_rate=%s sent_channels=%s",
                sid,
                url,
                containerized,
                q.get("encoding"),
                q.get("sample_rate"),
                q.get("channels"),
            )
        except Exception:
            logger.info("Deepgram connect start sid=%s url=%s", sid, url)

        if DG_TEST_MODE:
            self._ws = _FakeWSForTests()
            DG_LAST_URL = url
            cfg_payload = _initial_config(self._cfg)
            DG_LAST_CONFIG = _diagnostic_config(cfg_payload, self._cfg)
            self._open_evt.set()
            await self._signal_ready()
            logger.info("Deepgram test-mode connect sid=%s", sid)
            if callable(self._jlog):
                try:
                    self._jlog("dg_open", sid=sid, url=url, tag=self._url_tag, test_mode=True)
                except Exception:
                    pass
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
        await self.wait_socket_open(timeout=float(os.getenv('DG_OPEN_MICRO_WAIT_S','0.75')))

        cfg_payload = _initial_config(self._cfg)
        DG_LAST_URL = url
        DG_LAST_CONFIG = _diagnostic_config(cfg_payload, self._cfg)
        async with self._send_lock:
            await self._ws.send(json.dumps(cfg_payload))

        await self._signal_ready()
        self._rx_task = asyncio.create_task(self._rx_loop())

        if self._keepalive_interval > 0:
            self._keepalive_task = asyncio.create_task(self._keepalive_loop())

        logger.info("Deepgram connect ok sid=%s", sid)
        if callable(self._jlog):
            try:
                self._jlog(
                    "dg_open",
                    sid=sid,
                    url=url,
                    tag=self._url_tag,
                    containerized=containerized,
                )
            except Exception:
                pass

    async def close(
        self,
        wait_for_final: bool = True,
        timeout: Optional[float] = None,
        linger_ms: Optional[int] = None,
    ) -> None:
        if self._closed or self._closing:
            return
        self._closing = True
        sid = self._sid_for_log()
        logger.info(
            "Deepgram close start sid=%s wait_for_final=%s linger_ms=%s",
            sid,
            wait_for_final,
            linger_ms,
        )

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

        # Ensure any queued audio is flushed before CloseStream
        try:
            await self._flush_tx()
        except Exception:
            pass

        try:
            if self._ws and getattr(self._ws, "open", False):
                async with self._send_lock:
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
            ws = self._ws
            if ws and getattr(ws, "open", True):
                await ws.close()
        finally:
            self._ws = None

        try:
            task = self._rx_task
            self._rx_task = None
            if task:
                try:
                    task.cancel()
                except Exception:
                    pass
        finally:
            self._closed = True
            self._closing = False
            await self._stop_keepalive()

        logger.info("Deepgram close complete sid=%s", sid)
        if callable(self._jlog):
            try:
                self._jlog(
                    "dg_close",
                    sid=sid,
                    tag=self._url_tag,
                    wait_for_final=wait_for_final,
                    linger_ms=linger_ms,
                    had_result=self._had_result(),
                )
            except Exception:
                pass

    # -- sending ---------------------------------------------------------------

    async def send(self, chunk: bytes) -> None:
        if not isinstance(chunk, (bytes, bytearray)):
            raise TypeError("chunk must be bytes")
        if not chunk:
            return

        # Always enqueue first to avoid races; we'll send directly if possible.
        self._tx_queue.append(bytes(chunk))

        sid = self._sid_for_log()

        # If not yet "ready", schedule a flush once we are
        if not self._open_evt.is_set():
            try:
                await asyncio.wait_for(self._open_evt.wait(), timeout=self._open_wait_s)
            except asyncio.TimeoutError:
                if not self._open_gate_warned:
                    logger.warning("Deepgram send gated but no open within timeout sid=%s", sid)
                    self._open_gate_warned = True
                # Even if timed out, treat as ready to avoid deadlock; flush will re-check.
                await self._signal_ready()

        # Opportunistic flush now (won't throw if socket not open yet)
        self._schedule_flush()

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

                    # Prefer channel.* first (typical DG shape)
                    channel = msg.get("channel") or {}
                    alts = channel.get("alternatives")
                    if isinstance(alts, list) and alts:
                        text = (alts[0].get("transcript") or "").strip()

                    # Fallback: top-level alternatives (some messages)
                    if not text:
                        top_alts = msg.get("alternatives")
                        if isinstance(top_alts, list) and top_alts:
                            text = (top_alts[0].get("transcript") or "").strip()

                    # Fallback: top-level transcript (some messages)
                    if not text and isinstance(msg.get("transcript"), str):
                        text = (msg.get("transcript") or "").strip()

                    # Finalness can be on channel or top-level, or implied by event type
                    is_final = (
                        bool(channel.get("is_final"))
                        or bool(msg.get("is_final"))
                        or bool(msg.get("speech_final"))
                        or (evt_type in ("utteranceend", "UtteranceEnd"))
                    )

                    # No usable text in this message; keep listening
                    if not text:
                        continue

                    # Seeing a result also implies the upstream is functioning
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
                self._had_result(),
            )
            if not self._had_result():
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
            while True:
                if self._closed:
                    break
                ws = self._ws
                if not ws:
                    break
                if getattr(ws, "closed", False) or getattr(ws, "closing", False):
                    break
                if not getattr(ws, "open", False):
                    break

                try:
                    async with self._send_lock:
                        await ws.send(json.dumps({"type": "KeepAlive"}))
                    logger.debug("Deepgram keepalive sid=%s interval=%s", sid, self._keepalive_interval)
                except websockets.ConnectionClosed as exc:
                    logger.warning("Deepgram keepalive closed sid=%s err=%s", sid, exc)
                    break
                except Exception as exc:
                    logger.warning("Deepgram keepalive failed sid=%s err=%s", sid, exc)
                    try:
                        await asyncio.sleep(self._keepalive_interval)
                    except asyncio.CancelledError:
                        return
                    continue

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
