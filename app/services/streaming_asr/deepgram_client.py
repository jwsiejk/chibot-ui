from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import AsyncGenerator, Optional, Any, Deque, Tuple, Callable
from collections import deque

import websockets  # provided by uvicorn[standard]

try:  # pragma: no cover - exercised indirectly
    _WEBSOCKETS_PROTOCOL = websockets.protocol  # type: ignore[attr-defined]
except AttributeError:  # pragma: no cover - older packaging style
    try:
        from websockets import protocol as _WEBSOCKETS_PROTOCOL  # type: ignore
    except Exception:  # pragma: no cover - highly defensive
        _WEBSOCKETS_PROTOCOL = None  # type: ignore


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


class DeepgramDrainTimeoutError(RuntimeError):
    """Raised when the transmit queue fails to drain during close."""

    def __init__(
        self, sid: str, *, queued_chunks: int, queued_bytes: int, wait_timeout: bool
    ) -> None:
        self.sid = sid
        self.queued_chunks = queued_chunks
        self.queued_bytes = queued_bytes
        self.wait_timeout = wait_timeout
        super().__init__(
            f"drain_timeout sid={sid} queued_chunks={queued_chunks} queued_bytes={queued_bytes}"
        )


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
            + "&interim_results=true&vad_events=true&smart_format=true&punctuate=true"
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

        # Provide a sensible default for utterance_end_ms (2s) unless explicitly overridden
        qd.setdefault("utterance_end_ms", "2000")

        query = _p.urlencode(qd)
        base = _p.urlunsplit(
            (parts.scheme, parts.netloc, parts.path, query, parts.fragment)
        )
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
      • Drops tiny preamble chunk (<DG_MIN_VALID_BYTES) once, to avoid bogus data (raw only).
      • **Flushes all queued audio before CloseStream; close waits for drain.**
      • Sends {"type": "CloseStream"} and (optionally) waits briefly for final.
    """

    def __init__(
        self,
        _cfg: Optional[dict] = None,
        diag_hook: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._cfg = _cfg or {}
        self._ws = None  # type: ignore
        self._rx_task: Optional[asyncio.Task] = None
        self._ev_queue: asyncio.Queue = asyncio.Queue()
        self._closed = False
        self._closing = False
        self._url_tag: Optional[str] = None
        self._dg_id: int = id(self)

        hook_candidate: Optional[Callable[..., Any]] = None
        if callable(diag_hook):
            hook_candidate = diag_hook
        else:
            try:
                cfg_hook = self._cfg.get("_diag_hook") if isinstance(self._cfg, dict) else None
            except Exception:
                cfg_hook = None
            if callable(cfg_hook):
                hook_candidate = cfg_hook
        self._diag_hook: Optional[Callable[..., Any]] = hook_candidate
        self._diag_end_emitted: bool = False
        try:
            sid_val = None
            if isinstance(self._cfg, dict):
                sid_val = self._cfg.get("session_id") or self._cfg.get("sid")
            self._diag_session_id: Optional[str] = str(sid_val) if sid_val else None
        except Exception:
            self._diag_session_id = None
        try:
            tag_hint = None
            if isinstance(self._cfg, dict):
                for key in ("_url_tag", "dg_url_tag", "url_tag"):
                    val = self._cfg.get(key)
                    if val:
                        tag_hint = str(val)
                        break
            self._diag_tag_hint: Optional[str] = tag_hint
        except Exception:
            self._diag_tag_hint = None

        # TX queue + flushing
        self._tx_queue: Deque[bytes] = deque()
        self._flush_task: Optional[asyncio.Task] = None

        # Graceful shutdown coordination
        self._final_event: asyncio.Event = asyncio.Event()
        self._any_result: bool = False
        self._drain_event: asyncio.Event = asyncio.Event()
        self._drain_event.set()
        self._drain_timeout_s: float = float(
            os.getenv("DG_CLOSE_DRAIN_TIMEOUT_S", "1.5")
        )

        # First-chunk guard & timing
        self._first_real_sent: bool = False
        self._min_valid_bytes: int = int(os.getenv("DG_MIN_VALID_BYTES", "64"))
        self._last_chunk_ts: float = 0.0

        # Tunables
        self._linger_ms: int = int(
            os.getenv("DG_LINGER_MS", "800")
        )  # slightly longer default
        self._final_wait_s: float = float(os.getenv("DG_FINAL_WAIT_S", "12"))

        # Open gate
        self._open_evt: asyncio.Event = asyncio.Event()
        self._asr_open_emitted: bool = False
        self._open_wait_s: float = float(os.getenv("DG_OPEN_WAIT_S", "3.0"))
        self._open_gate_warned: bool = False

        # Keepalive
        self._keepalive_task: Optional[asyncio.Task] = None
        self._keepalive_interval: float = float(
            os.getenv("DG_KEEPALIVE_INTERVAL_S", "5.0")
        )

        # Optional JSON logger injected by WS layer (ws_asgi)
        self._jlog = self._cfg.get("_jlog")

        # Serialize outbound websocket sends to avoid concurrent writer errors
        self._send_lock = asyncio.Lock()

    def _diag_payload(self, **extra: Any) -> dict:
        payload: dict[str, Any] = {"provider": "deepgram"}
        if self._diag_session_id:
            payload["session_id"] = self._diag_session_id
        tag = self._url_tag or self._diag_tag_hint
        if tag:
            payload["tag"] = tag
        payload.update(extra)
        return payload

    def _emit_diag(self, label: str, **extra: Any) -> None:
        hook = self._diag_hook
        if not callable(hook):
            return
        payload = self._diag_payload(**extra)
        try:
            hook(label, **payload)
        except TypeError:
            try:
                hook(label, payload)
            except Exception:
                pass
        except Exception:
            pass

    def _ws_is_open(self, ws: Optional[Any] = None) -> bool:
        """Best-effort detection for whether the websocket is open."""

        target = ws if ws is not None else self._ws
        if not target:
            return False

        try:
            open_attr = getattr(target, "open", None)
            if open_attr:
                return True
        except Exception:
            pass

        proto_mod = _WEBSOCKETS_PROTOCOL
        state = None
        if proto_mod is not None:
            try:
                state = getattr(target, "state", None)
            except Exception:
                state = None
            if state is not None:
                try:
                    open_state = getattr(proto_mod, "OPEN", None)
                    if open_state is not None and state == open_state:
                        return True
                except Exception:
                    pass
                try:
                    state_cls = getattr(proto_mod, "State", None)
                    open_member = (
                        getattr(state_cls, "OPEN", None) if state_cls else None
                    )
                    if open_member is not None and state == open_member:
                        return True
                except Exception:
                    pass

        if state is None:
            try:
                closed_attr = getattr(target, "closed", None)
                if isinstance(closed_attr, bool):
                    return not closed_attr
            except Exception:
                pass

        return False

    def is_open(self) -> bool:
        """Return True if the underlying websocket appears open."""
        try:
            return self._ws_is_open()
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
            self._emit_diag("asr_open", active=True)
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
        if self._ws_is_open():
            return True
        end = time.time() + timeout
        while time.time() < end:
            if self._ws_is_open():
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
                logger.debug(
                    "Deepgram flush_tx raised; will retry on next trigger",
                    exc_info=True,
                )

        self._flush_task = asyncio.create_task(_runner())

    async def _flush_tx(self) -> Tuple[int, Optional[str]]:
        """Drain queued audio if the socket is open and we've signaled ready."""
        sid = self._sid_for_log()
        queued_at_start = len(self._tx_queue)
        ws_open_flag = self._ws_is_open()
        open_evt_set = self._open_evt.is_set()

        logger.debug(
            "Deepgram flush enter sid=%s queued=%s ws_open=%s open_evt=%s",
            sid,
            queued_at_start,
            ws_open_flag,
            open_evt_set,
        )
        if callable(self._jlog):
            try:
                self._jlog(
                    "dg_flush_enter",
                    sid=sid,
                    dg_id=self._dg_id,
                    tag=self._url_tag,
                    queued=queued_at_start,
                    ws_open=ws_open_flag,
                    open_evt=open_evt_set,
                )
            except Exception:
                pass

        if not self._tx_queue:
            if not self._drain_event.is_set():
                self._drain_event.set()
            ws_open_after = self._ws_is_open()
            if callable(self._jlog):
                try:
                    self._jlog(
                        "dg_flush_exit",
                        sid=sid,
                        dg_id=self._dg_id,
                        tag=self._url_tag,
                        queued=len(self._tx_queue),
                        sent_bytes=0,
                        sent_chunks=0,
                        ws_open=ws_open_after,
                        open_evt=self._open_evt.is_set(),
                        first8_hex=None,
                    )
                except Exception:
                    pass
            logger.debug(
                "Deepgram flush exit sid=%s queued=%s sent_bytes=%s",
                sid,
                len(self._tx_queue),
                0,
            )
            return 0, None
        # Wait until socket open — don't raise; just give it a short chance
        await self.wait_socket_open(
            timeout=float(os.getenv("DG_OPEN_MICRO_WAIT_S", "0.75"))
        )
        if not self._ws_is_open():
            if callable(self._jlog):
                try:
                    self._jlog(
                        "dg_flush_exit",
                        sid=sid,
                        dg_id=self._dg_id,
                        tag=self._url_tag,
                        queued=len(self._tx_queue),
                        sent_bytes=0,
                        sent_chunks=0,
                        ws_open=False,
                        open_evt=self._open_evt.is_set(),
                        first8_hex=None,
                    )
                except Exception:
                    pass
            logger.debug(
                "Deepgram flush exit sid=%s queued=%s sent_bytes=%s ws_open=%s",
                sid,
                len(self._tx_queue),
                0,
                False,
            )
            return 0, None
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
        total_sent = 0
        sent_chunks = 0
        first_chunk: Optional[bytes] = None
        while self._tx_queue and self._ws_is_open():
            ws = self._ws
            if ws is None:
                break
            data = self._tx_queue[0]
            # Drop tiny preamble once (RAW only)
            if (
                not containerized
                and (not self._first_real_sent)
                and len(data) < self._min_valid_bytes
            ):
                logger.debug(
                    "Deepgram drop small queued chunk sid=%s bytes=%s min_bytes=%s",
                    sid,
                    len(data),
                    self._min_valid_bytes,
                )
                self._tx_queue.popleft()
                continue
            try:
                async with self._send_lock:
                    await ws.send(data)
                self._first_real_sent = True
                self._last_chunk_ts = time.time()
                self._tx_queue.popleft()
                total_sent += len(data)
                sent_chunks += 1
                if first_chunk is None:
                    first_chunk = data
                logger.debug(
                    "Deepgram sent chunk (flush) sid=%s bytes=%s queued=%s",
                    sid,
                    len(data),
                    len(self._tx_queue),
                )
                if callable(self._jlog):
                    try:
                        self._jlog(
                            "dg_forward",
                            sid=sid,
                            dg_id=self._dg_id,
                            tag=self._url_tag,
                            bytes=len(data),
                            queued=len(self._tx_queue),
                            total_sent=total_sent,
                        )
                    except Exception:
                        pass
            except Exception as e:
                # Transient send issue; stop and retry on next trigger
                logger.debug(
                    "Deepgram deferred send sid=%s err=%s queued=%s",
                    sid,
                    type(e).__name__,
                    len(self._tx_queue),
                )
                break
        if not self._tx_queue and not self._drain_event.is_set():
            self._drain_event.set()
        first8_hex: Optional[str] = None
        if first_chunk:
            try:
                first8_hex = first_chunk[:8].hex()
            except Exception:
                first8_hex = None
        ws_open_after = self._ws_is_open()
        open_evt_after = self._open_evt.is_set()
        if callable(self._jlog):
            try:
                self._jlog(
                    "dg_flush_exit",
                    sid=sid,
                    dg_id=self._dg_id,
                    tag=self._url_tag,
                    queued=len(self._tx_queue),
                    sent_bytes=total_sent,
                    sent_chunks=sent_chunks,
                    ws_open=ws_open_after,
                    open_evt=open_evt_after,
                    first8_hex=first8_hex,
                )
            except Exception:
                pass
        logger.debug(
            "Deepgram flush exit sid=%s queued=%s sent_bytes=%s sent_chunks=%s ws_open=%s open_evt=%s",
            sid,
            len(self._tx_queue),
            total_sent,
            sent_chunks,
            ws_open_after,
            open_evt_after,
        )
        return total_sent, first8_hex

    # -- lifecycle -------------------------------------------------------------

    async def connect(self) -> None:
        global DG_LAST_URL, DG_LAST_CONFIG
        sid = self._sid_for_log()
        if callable(self._jlog):
            try:
                self._jlog(
                    "dg_connect_begin",
                    sid=sid,
                    dg_id=self._dg_id,
                    tag=self._url_tag,
                    already_open=bool(self._ws),
                    closing=self._closing,
                    closed=self._closed,
                )
            except Exception:
                pass
        if self._ws:
            logger.debug("Deepgram connect skipped sid=%s already has websocket", sid)
            return

        url = _dg_url(self._cfg)
        self._emit_diag("provider_open", active=True, url=url)

        containerized = False

        try:
            # Diagnostic: parse params and emit compact JSON log that shows
            # whether we omitted encoding/sample_rate/channels (containerized) or sent raw.
            import urllib.parse as _p

            parts = _p.urlsplit(url)
            q = dict(_p.parse_qsl(parts.query, keep_blank_values=True))
            self._url_tag = q.get("tag") or None
            transport = (self._cfg or {}).get("_transport", {}) or {}
            containerized = bool(transport.get("containerized_opus"))
            url_meta = {
                "container": transport.get("container"),
                "codec": transport.get("codec"),
                "containerized_opus": containerized,
                "normalized_pcm": bool(transport.get("normalized_pcm")),
                "raw_fallback": bool(transport.get("raw_fallback")),
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

            # Build sanitized query string for telemetry without sensitive params
            sanitized_pairs = []
            raw_param_keys = {"encoding", "sample_rate", "channels"}
            for key in sorted(q.keys()):
                if key is None:
                    continue
                key_str = str(key)
                lower = key_str.lower()
                if "key" in lower or "token" in lower or "secret" in lower:
                    continue
                if containerized and key_str in raw_param_keys:
                    continue
                sanitized_pairs.append((key_str, q[key]))
            sanitized_query = _p.urlencode(sanitized_pairs, doseq=True)
            sanitized_qs = f"?{sanitized_query}" if sanitized_query else "?"
            raw_params_absent = all(param not in q for param in raw_param_keys)

            # Structured JSON log if _jlog is available (preferred for your admin viewer)
            if callable(self._jlog):
                try:
                    self._jlog(
                        "dg_url_sanitized",
                        sid=sid,
                        dg_id=self._dg_id,
                        qs=sanitized_qs,
                        containerized_opus=containerized,
                        raw_params_absent=raw_params_absent,
                    )
                except Exception:
                    pass
                try:
                    self._jlog(
                        "asr_url",
                        dg_id=self._dg_id,
                        url=url,
                        container=url_meta.get("container"),
                        codec=url_meta.get("codec"),
                        containerized_opus=bool(url_meta.get("containerized_opus")),
                        normalized_pcm=bool(url_meta.get("normalized_pcm")),
                        raw_fallback=bool(url_meta.get("raw_fallback")),
                        omitted_params=url_meta.get("omitted_params"),
                        raw_params=url_meta.get("raw_params"),
                    )
                except Exception:
                    pass

            # Keep existing human-readable info log
            logger.info(
                "dg_ws_connect sid=%s url=%s containerized_opus=%s normalized_pcm=%s raw_fallback=%s sent_encoding=%s sent_sample_rate=%s sent_channels=%s",
                sid,
                url,
                containerized,
                url_meta.get("normalized_pcm"),
                url_meta.get("raw_fallback"),
                q.get("encoding"),
                q.get("sample_rate"),
                q.get("channels"),
            )
        except Exception:
            logger.info("Deepgram connect start sid=%s url=%s", sid, url)

        start_ts = time.time()
        if callable(self._jlog):
            try:
                self._jlog(
                    "asr_connect_start",
                    sid=sid,
                    dg_id=self._dg_id,
                )
            except Exception:
                pass

        try:
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
                        self._jlog(
                            "dg_open",
                            sid=sid,
                            dg_id=self._dg_id,
                            url=url,
                            tag=self._url_tag,
                            test_mode=True,
                        )
                    except Exception:
                        pass
                    try:
                        elapsed_ms = int((time.time() - start_ts) * 1000)
                    except Exception:
                        elapsed_ms = 0
                    try:
                        self._jlog(
                            "asr_connect_ok",
                            sid=sid,
                            dg_id=self._dg_id,
                            elapsed_ms=elapsed_ms,
                        )
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
            await self.wait_socket_open(
                timeout=float(os.getenv("DG_OPEN_MICRO_WAIT_S", "0.75"))
            )

            cfg_payload = _initial_config(self._cfg)
            DG_LAST_URL = url
            DG_LAST_CONFIG = _diagnostic_config(cfg_payload, self._cfg)
            async with self._send_lock:
                await self._ws.send(json.dumps(cfg_payload))
            if callable(self._jlog):
                try:
                    self._jlog(
                        "dg_config_sent",
                        sid=sid,
                        dg_id=self._dg_id,
                        tag=self._url_tag,
                        keys=sorted(cfg_payload.keys()),
                    )
                except Exception:
                    pass

            await self._signal_ready()
            # Proactively schedule a flush in case audio was queued before/while connecting
            self._schedule_flush(delay=0.0)

            self._rx_task = asyncio.create_task(self._rx_loop())

            if self._keepalive_interval > 0:
                self._keepalive_task = asyncio.create_task(self._keepalive_loop())

            logger.info("Deepgram connect ok sid=%s", sid)
            if callable(self._jlog):
                try:
                    self._jlog(
                        "dg_open",
                        sid=sid,
                        dg_id=self._dg_id,
                        url=url,
                        tag=self._url_tag,
                        containerized=containerized,
                    )
                except Exception:
                    pass
                try:
                    elapsed_ms = int((time.time() - start_ts) * 1000)
                except Exception:
                    elapsed_ms = 0
                try:
                    self._jlog(
                        "asr_connect_ok",
                        sid=sid,
                        dg_id=self._dg_id,
                        elapsed_ms=elapsed_ms,
                    )
                except Exception:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if callable(self._jlog):
                try:
                    elapsed_ms = int((time.time() - start_ts) * 1000)
                except Exception:
                    elapsed_ms = 0
                try:
                    self._jlog(
                        "asr_connect_err",
                        sid=sid,
                        dg_id=self._dg_id,
                        elapsed_ms=elapsed_ms,
                        code=exc.__class__.__name__,
                    )
                except Exception:
                    pass
            self._emit_diag(
                "asr_error",
                error=f"connect:{exc.__class__.__name__}",
                detail=_clip_text(str(exc), 200),
                url=url,
            )
            raise

    async def close(
        self,
        wait_for_final: bool = True,
        timeout: Optional[float] = None,
        linger_ms: Optional[int] = None,
    ) -> None:
        """Graceful shutdown:
        1) Drain queued audio (budget-based retry) BEFORE CloseStream
        2) Send CloseStream
        3) Optionally wait for final
        4) Close socket
        """
        if self._closed:
            if not self._diag_end_emitted:
                self._emit_diag("end", active=False, had_result=self._had_result())
                self._diag_end_emitted = True
            return
        if self._closing:
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

        # If we've never sent a chunk but have queued bytes, add a tiny settle delay
        # so the configure→ready gate can complete before our first flush attempt.
        if self._last_chunk_ts == 0 and self._tx_queue and linger_ms > 0:
            try:
                await asyncio.sleep(min(0.12, linger_ms / 1000.0))
            except asyncio.CancelledError:
                pass

        # Optional linger relative to the last successfully sent chunk
        if self._last_chunk_ts > 0 and linger_ms > 0:
            elapsed_ms = int((time.time() - self._last_chunk_ts) * 1000)
            delay_ms = max(0, linger_ms - elapsed_ms)
            if delay_ms > 0:
                try:
                    await asyncio.sleep(delay_ms / 1000.0)
                except asyncio.CancelledError:
                    pass

        flush_bytes = 0
        flush_first8: Optional[str] = None
        dropped_chunks = 0
        dropped_bytes = 0
        drain_wait_timeout = False
        drain_failed = False

        if callable(self._jlog):
            try:
                self._jlog(
                    "dg_close_drain_begin",
                    sid=sid,
                    dg_id=self._dg_id,
                    tag=self._url_tag,
                    queued=len(self._tx_queue),
                    linger_ms=linger_ms,
                    wait_for_final=wait_for_final,
                    drain_timeout_s=self._drain_timeout_s,
                )
            except Exception:
                pass

        def _record_flush(bytes_sent: int, first_hex: Optional[str]) -> None:
            nonlocal flush_bytes, flush_first8
            if bytes_sent:
                flush_bytes += bytes_sent
            if first_hex and flush_first8 is None:
                flush_first8 = first_hex

        # Always attempt an initial flush
        try:
            bytes_sent, first_hex = await self._flush_tx()
            _record_flush(bytes_sent, first_hex)
        except Exception:
            pass

        # Wait briefly for drain acknowledgement before the retry loop
        if self._tx_queue and self._drain_timeout_s > 0:
            try:
                await asyncio.wait_for(
                    self._drain_event.wait(), timeout=self._drain_timeout_s
                )
            except asyncio.TimeoutError:
                drain_wait_timeout = True

        # Budget-based retry loop to ensure first-chunk drain under load
        budget_s = float(os.getenv("DG_CLOSE_FLUSH_BUDGET_S", "3.0"))
        recheck_s = float(os.getenv("DG_CLOSE_FLUSH_RECHECK_S", "0.15"))
        deadline = time.time() + budget_s

        while self._tx_queue and time.time() < deadline:
            # Wait for provider open gate if needed (bounded by remaining budget)
            if not self._open_evt.is_set():
                remaining = max(0.05, deadline - time.time())
                try:
                    await asyncio.wait_for(
                        self._open_evt.wait(), timeout=min(self._open_wait_s, remaining)
                    )
                except asyncio.TimeoutError:
                    pass

            # Try to ensure socket is actually open
            remaining = max(0.05, deadline - time.time())
            try:
                await self.wait_socket_open(timeout=min(0.5, remaining))
            except Exception:
                pass

            try:
                bytes_sent, first_hex = await self._flush_tx()
                _record_flush(bytes_sent, first_hex)
            except Exception:
                break

            if self._tx_queue:
                try:
                    await asyncio.sleep(
                        min(recheck_s, max(0.05, deadline - time.time()))
                    )
                except asyncio.CancelledError:
                    break

        if self._tx_queue:
            dropped_chunks = len(self._tx_queue)
            try:
                dropped_bytes = sum(len(chunk) for chunk in self._tx_queue)
            except Exception:
                dropped_bytes = 0
            drain_failed = True
            logger.warning(
                "Deepgram close drain timeout sid=%s queued_chunks=%s queued_bytes=%s wait_timeout=%s",
                sid,
                dropped_chunks,
                dropped_bytes,
                drain_wait_timeout,
            )
            if callable(self._jlog):
                try:
                    self._jlog(
                        "dg_writer_timeout",
                        sid=sid,
                        dg_id=self._dg_id,
                        tag=self._url_tag,
                        queued_chunks=dropped_chunks,
                        queued_bytes=dropped_bytes,
                        wait_timeout=drain_wait_timeout,
                        attempts="budget",
                    )
                except Exception:
                    pass

        drain_exc: Optional[DeepgramDrainTimeoutError] = None

        try:
            logger.info(
                "dg_writer_drained sid=%s bytes=%s first8_hex=%s queued=%s",
                sid,
                flush_bytes,
                flush_first8,
                len(self._tx_queue),
            )
            if callable(self._jlog):
                try:
                    self._jlog(
                        "dg_writer_drained",
                        sid=sid,
                        dg_id=self._dg_id,
                        tag=self._url_tag,
                        bytes=flush_bytes,
                        first8_hex=flush_first8,
                        queued=len(self._tx_queue),
                        dropped_chunks=dropped_chunks,
                        dropped_bytes=dropped_bytes,
                    )
                except Exception:
                    pass

            # Now send CloseStream after we've flushed all audio
            try:
                ws = self._ws
                if self._ws_is_open(ws):
                    async with self._send_lock:
                        if callable(self._jlog):
                            try:
                                self._jlog(
                                    "dg_send_close_stream",
                                    sid=sid,
                                    dg_id=self._dg_id,
                                    tag=self._url_tag,
                                    queued=len(self._tx_queue),
                                )
                            except Exception:
                                pass
                        await ws.send(json.dumps({"type": "CloseStream"}))
            except Exception:
                pass

            if wait_for_final:
                if timeout is None:
                    timeout = self._final_wait_s
                try:
                    await asyncio.wait_for(self._final_event.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    err_txt = f"final_timeout:{timeout}s"
                    self._emit_diag(
                        "asr_error",
                        error=err_txt,
                        final_timeout=timeout,
                    )
                    try:
                        await self._ev_queue.put({"type": "asr_error", "error": err_txt})
                    except Exception:
                        pass

            try:
                ws = self._ws
                if ws and (self._ws_is_open(ws) or not hasattr(ws, "open")):
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
                        dg_id=self._dg_id,
                        tag=self._url_tag,
                        wait_for_final=wait_for_final,
                        linger_ms=linger_ms,
                        had_result=self._had_result(),
                        drain_failed=drain_failed,
                    )
                except Exception:
                    pass

            if drain_failed:
                drain_exc = DeepgramDrainTimeoutError(
                    sid,
                    queued_chunks=dropped_chunks,
                    queued_bytes=dropped_bytes,
                    wait_timeout=drain_wait_timeout,
                )
        finally:
            if not self._diag_end_emitted:
                self._emit_diag(
                    "end",
                    active=False,
                    had_result=self._had_result(),
                    drain_failed=drain_failed,
                    queued_chunks=dropped_chunks,
                    queued_bytes=dropped_bytes,
                    wait_timeout=drain_wait_timeout,
                )
                self._diag_end_emitted = True

        if drain_exc:
            self._emit_diag(
                "asr_error",
                error="drain_timeout",
                queued_chunks=dropped_chunks,
                queued_bytes=dropped_bytes,
                wait_timeout=drain_wait_timeout,
                active=False,
            )
            raise drain_exc

        # -- sending ---------------------------------------------------------------

    async def send(self, chunk: bytes) -> None:
        if not isinstance(chunk, (bytes, bytearray)):
            raise TypeError("chunk must be bytes")
        if not chunk:
            return

        sid = self._sid_for_log()
        payload = bytes(chunk)
        self._tx_queue.append(payload)
        if self._drain_event.is_set():
            self._drain_event.clear()
        if callable(self._jlog):
            try:
                self._jlog(
                    "dg_tx_enqueue",
                    sid=sid,
                    dg_id=self._dg_id,
                    tag=self._url_tag,
                    bytes=len(payload),
                    queued=len(self._tx_queue),
                )
            except Exception:
                pass

        # Lazy preconnect: if socket isn't open yet, kick off connect in the background.
        if not self.is_open() and not self._closing:
            lazy_started = False
            try:
                asyncio.create_task(self.connect())
                lazy_started = True
            except Exception:
                pass
            if lazy_started and callable(self._jlog):
                try:
                    self._jlog(
                        "dg_lazy_connect",
                        sid=sid,
                        dg_id=self._dg_id,
                        tag=self._url_tag,
                        queued=len(self._tx_queue),
                    )
                except Exception:
                    pass

        if not self._open_evt.is_set():
            try:
                await asyncio.wait_for(self._open_evt.wait(), timeout=self._open_wait_s)
            except asyncio.TimeoutError:
                # Treat a gated send as a transport failure when the open event never arrived.
                if not self._open_gate_warned:
                    logger.warning(
                        "Deepgram send gated but no open within timeout sid=%s queued=%s",
                        sid,
                        len(self._tx_queue),
                    )
                    self._open_gate_warned = True
                if self._tx_queue:
                    try:
                        self._tx_queue.pop()
                    except Exception:
                        self._tx_queue.clear()
                if not self._tx_queue and not self._drain_event.is_set():
                    self._drain_event.set()
                raise RuntimeError("deepgram_not_connected:open_gate_timeout")

        # Opportunistic flush now (won't raise if socket not open yet)
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
                    msg = (
                        json.loads(raw)
                        if isinstance(raw, (str, bytes, bytearray))
                        else raw
                    )
                except Exception:
                    continue

                evt_type = (msg.get("type") or "").lower()

                if evt_type in ("metadata", "listening", "connected", "ready"):
                    await self._signal_ready()
                    continue

                if evt_type in (
                    "results",
                    "transcript",
                    "partialtranscript",
                    "speech.update",
                ):
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
                    self._emit_diag(
                        "asr_final" if is_final else "asr_partial",
                        text=text,
                        chars=len(text),
                        active=True,
                    )
                    try:
                        await self._ev_queue.put(
                            {
                                "type": "user_final" if is_final else "user_partial",
                                "text": text,
                            }
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
                    self._emit_diag(
                        "asr_error",
                        error=msg.get("error") or evt_type,
                        provider_error=True,
                        detail=_clip_text(str(msg), 200),
                    )
                    try:
                        await self._ev_queue.put(
                            {"type": "asr_error", "error": msg.get("error") or evt_type}
                        )
                    except Exception:
                        pass

                else:
                    logger.debug(
                        "Deepgram unhandled event sid=%s evt_type=%s",
                        sid,
                        evt_type or "unknown",
                    )
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
                err_txt = f"recv_closed:{getattr(e, 'code', '')}:{getattr(e, 'reason', '')}"
                self._emit_diag(
                    "asr_error",
                    error=err_txt,
                    provider_error=True,
                    code=getattr(e, "code", None),
                    reason=getattr(e, "reason", ""),
                )
                try:
                    await self._ev_queue.put(
                        {
                            "type": "asr_error",
                            "error": err_txt,
                        }
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.exception("Deepgram rx loop error sid=%s", sid)
            self._emit_diag(
                "asr_error",
                error=f"rx:{e.__class__.__name__}",
                provider_error=True,
            )
            try:
                await self._ev_queue.put(
                    {"type": "asr_error", "error": f"rx:{e.__class__.__name__}"}
                )
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
                if not self._ws_is_open(ws):
                    break

                try:
                    async with self._send_lock:
                        await ws.send(json.dumps({"type": "KeepAlive"}))
                    logger.debug(
                        "Deepgram keepalive sid=%s interval=%s",
                        sid,
                        self._keepalive_interval,
                    )
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
