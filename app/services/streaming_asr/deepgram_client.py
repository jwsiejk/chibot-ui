from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import warnings
from typing import AsyncGenerator, Optional, Any, Deque, Tuple, Callable
from collections import deque

import websockets  # provided by uvicorn[standard]

from app.flow.emit import emit as flow_emit
from app.obs.source_tags import make_source_meta
from app.ws.bus import bus as stream_bus

from .asr_metrics import record_turn_metrics

try:
    from app.admin_log import emit as _admin_emit
except Exception:  # pragma: no cover - extremely defensive

    def _admin_emit(*a, **k):  # type: ignore[empty-body]
        pass

try:
    from websockets import exceptions as _ws_exceptions  # type: ignore[attr-defined]
except Exception:  # pragma: no cover - extremely defensive
    _ws_exceptions = None  # type: ignore[assignment]
else:
    _invalid_types = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        for _name in ("InvalidStatus", "InvalidStatusCode"):
            _cls = getattr(_ws_exceptions, _name, None)
            if isinstance(_cls, type):
                _invalid_types.append(_cls)
    _INVALID_STATUS_TYPES = tuple(_invalid_types)
if "_INVALID_STATUS_TYPES" not in globals():
    _INVALID_STATUS_TYPES = ()

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


class _NullLogger:
    def debug(self, *args, **kwargs):
        return None

    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None

    def exception(self, *args, **kwargs):
        return None

_DG_KEY_PROBED = False


def _mask_key_fragment(raw: str) -> str:
    try:
        txt = str(raw)
    except Exception:
        return "?"
    txt = txt.strip()
    if not txt:
        return "?"
    if len(txt) <= 4:
        return "*" * len(txt)
    return f"{txt[:2]}…{txt[-2:]}"


def _api_key_info(*, log: bool = True, logger_obj: logging.Logger = logger) -> tuple[str, int]:
    key = os.getenv("DEEPGRAM_API_KEY", "")
    if key is None:
        key = ""
    key = str(key).strip()
    if not key:
        if DG_TEST_MODE:
            return "test", 4
        raise RuntimeError("DEEPGRAM_API_KEY is not set")
    length = len(key)
    global _DG_KEY_PROBED
    if log and not _DG_KEY_PROBED:
        _DG_KEY_PROBED = True
        try:
            masked = _mask_key_fragment(key)
            logger_obj.info("Deepgram API key detected len=%s mask=%s", length, masked)
        except Exception:
            logger_obj.info("Deepgram API key detected len=%s", length)
    return key, length


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


def _safe_float(val):
    try:
        if val is None:
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _coerce_speech_ms(source: Any) -> Optional[int]:
    """Best-effort conversion of Deepgram speech metadata to milliseconds."""
    if not isinstance(source, dict):
        return None

    for key in ("duration_ms", "durationMs", "speech_ms"):
        val = source.get(key)
        if val is None:
            continue
        try:
            ms_val = int(max(0.0, float(val)))
        except (TypeError, ValueError):
            continue
        else:
            return ms_val

    duration_val = source.get("duration")
    if duration_val is None:
        end_val = source.get("end")
        start_val = (
            source.get("start")
            if source.get("start") is not None
            else source.get("begin")
        )
        if start_val is None:
            start_val = source.get("offset")
        if end_val is not None and start_val is not None:
            try:
                duration_val = float(end_val) - float(start_val)
            except Exception:
                duration_val = None
    if duration_val is not None:
        try:
            dur_float = float(duration_val)
        except (TypeError, ValueError):
            dur_float = None
        if dur_float is not None and dur_float >= 0.0:
            # Assume seconds unless clearly in milliseconds already
            if dur_float >= 50.0:
                return int(dur_float)
            return int(dur_float * 1000.0)

    return None


_flag_raw = os.getenv("DG_CONTAINERIZED_INCLUDE_ENCODING", "")
_CONTAINER_ENCODING_FLAG = _flag_raw.strip().lower() in {"1", "true", "yes", "on"}


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

    if isinstance(overrides, dict):
        try:
            transport_cfg = overrides.setdefault("_transport", {})
            if not isinstance(transport_cfg, dict):
                transport_cfg = {}
                overrides["_transport"] = transport_cfg
        except Exception:
            transport_cfg = {}

        if not containerized:
            transport_cfg["containerized_opus"] = True
            transport_cfg["_containerized_forced"] = True
            containerized = True

        transport_cfg.setdefault("container", "webm")
        transport_cfg.setdefault("codec", "opus")

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
    effective_utterance_end_ms = 0

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

        # Ensure default feature flags are present without overwriting explicit overrides
        qd.setdefault("interim_results", "true")
        qd.setdefault("vad_events", "true")
        qd.setdefault("smart_format", "true")
        qd.setdefault("punctuate", "true")

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

        # Provide a sensible default for utterance_end_ms unless explicitly overridden
        default_utterance = os.getenv("DEEPGRAM_UTTERANCE_END_MS", "3000")
        if containerized:
            default_utterance = "1200"
        qd.setdefault("utterance_end_ms", default_utterance)

        if containerized:
            if not _CONTAINER_ENCODING_FLAG:
                qd.pop("encoding", None)
            else:
                qd.setdefault("encoding", "opus")

        interim_val = qd.get("interim_results")
        interim_false = False
        try:
            if isinstance(interim_val, bool):
                interim_false = not interim_val
            elif interim_val is None:
                interim_false = False
            else:
                interim_false = str(interim_val).strip().lower() in {"false", "0"}
        except Exception:
            interim_false = False
        if interim_false:
            qd.pop("utterance_end_ms", None)
            effective_utterance_end_ms = 0
        else:
            try:
                val = qd.get("utterance_end_ms")
                if val is not None:
                    effective_utterance_end_ms = int(str(val))
            except Exception:
                effective_utterance_end_ms = 0

        query = _p.urlencode(qd)
        base = _p.urlunsplit(
            (parts.scheme, parts.netloc, parts.path, query, parts.fragment)
        )
    except Exception:
        pass

    if isinstance(overrides, dict):
        try:
            overrides["_effective_utterance_end_ms"] = int(
                effective_utterance_end_ms or 0
            )
        except Exception:
            overrides["_effective_utterance_end_ms"] = 0

    return base


def _auth_header(*, log: bool = True, logger_obj: logging.Logger = logger) -> str:
    key, _ = _api_key_info(log=log, logger_obj=logger_obj)
    return f"Token {key}"


def _client_url_metadata(url: str, overrides: Optional[dict]) -> tuple[str, str, dict[str, Any]]:
    """Return (safe_url, sanitized_query, meta) for diagnostics/clients."""

    transport = {}
    try:
        if overrides and isinstance(overrides.get("_transport"), dict):
            transport = dict(overrides.get("_transport") or {})
    except Exception:
        transport = {}

    containerized = bool(transport.get("containerized_opus"))
    container = transport.get("container")
    codec = transport.get("codec")
    normalized_pcm = bool(transport.get("normalized_pcm"))

    safe_url = url
    sanitized_qs = "?"
    meta: dict[str, Any] = {
        "container": container,
        "codec": codec,
        "containerized_opus": containerized,
        "normalized_pcm": normalized_pcm,
        "omitted_params": None,
        "raw_params": None,
    }

    try:
        import urllib.parse as _p

        parts = _p.urlsplit(url)
        q = dict(_p.parse_qsl(parts.query, keep_blank_values=True))

        raw_param_keys = {"encoding", "sample_rate", "channels"}
        if containerized:
            omitted = []
            for key in raw_param_keys:
                if key not in q:
                    omitted.append(key)
            meta["omitted_params"] = omitted
        else:
            meta["raw_params"] = {
                "encoding": q.get("encoding"),
                "sample_rate": q.get("sample_rate"),
                "channels": q.get("channels"),
            }

        sanitized_pairs = []
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
        safe_url = f"{parts.scheme}://{parts.netloc}{parts.path}"
        if sanitized_query:
            safe_url = f"{safe_url}?{sanitized_query}"
        if parts.fragment:
            safe_url = f"{safe_url}#{parts.fragment}"
    except Exception:
        pass

    return safe_url, sanitized_qs, meta


def build_client_session_descriptor(overrides: Optional[dict] = None) -> dict[str, Any]:
    """Expose Deepgram session info for browser-initiated clients."""

    cfg = dict(overrides or {})
    url = _dg_url(cfg)
    safe_url, sanitized_qs, meta = _client_url_metadata(url, cfg)
    configure_payload = _initial_config(cfg)

    try:
        key, _ = _api_key_info(log=False, logger_obj=logger)
        auth_masked = _mask_key_fragment(key)
    except Exception:
        auth_masked = None

    descriptor: dict[str, Any] = {
        "url": url,
        "sanitized_url": safe_url,
        "sanitized_query": sanitized_qs,
        "configure": configure_payload,
        "transport": {
            "container": meta.get("container"),
            "codec": meta.get("codec"),
            "containerized": bool(meta.get("containerized_opus")),
            "normalized_pcm": bool(meta.get("normalized_pcm")),
        },
        "meta": meta,
        "auth": {
            "header": "Authorization",
            "scheme": "Token",
            "masked": auth_masked,
            "requires_proxy": True,
        },
    }

    return descriptor


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
        self._recover_pending: bool = False
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

        try:
            raw_logging_enabled = None
            if isinstance(self._cfg, dict):
                raw_logging_enabled = self._cfg.get("advanced_logging_enabled")
        except Exception:
            raw_logging_enabled = None
        if raw_logging_enabled is None:
            raw_logging_enabled = True
        self._advanced_logging_enabled: bool = bool(raw_logging_enabled)
        self._logger: logging.Logger | _NullLogger = (
            logger if self._advanced_logging_enabled else _NullLogger()
        )

        # TX queue + flushing
        self._tx_queue: Deque[bytes] = deque()
        self._flush_task: Optional[asyncio.Task] = None
        self._auto_close_task: Optional[asyncio.Task] = None
        self._auto_close_requested: bool = False

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
        self._bytes_forwarded: int = 0
        self._min_valid_bytes: int = int(os.getenv("DG_MIN_VALID_BYTES", "64"))
        self._last_chunk_ts: float = 0.0
        self._last_transcript: str = ""

        # Tunables
        self._linger_ms: int = int(
            os.getenv("DG_LINGER_MS", "800")
        )  # slightly longer default
        self._final_wait_s: float = float(os.getenv("DG_FINAL_WAIT_S", "12"))

        # Open gate
        self._open_evt: asyncio.Event = asyncio.Event()
        self._asr_open_emitted: bool = False
        self._asr_start_emitted: bool = False
        self._asr_connect_emitted: bool = False
        self._asr_ready_emitted: bool = False
        self._backend_ready_observed: bool = False
        self._ready_observed: bool = False
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

        # Per-turn metrics tracking
        self._turn_counter: int = 0
        self._turn_current_id: Optional[int] = None
        self._turn_active: bool = False
        self._turn_start_ts: float = 0.0
        self._turn_first_partial_ts: float = 0.0
        self._turn_final_ts: float = 0.0
        self._turn_bytes_baseline: int = 0
        self._turn_metrics_recorded: bool = False
        self._turn_saw_1011: bool = False
        self._turn_last_close_code: Optional[int] = None
        self._turn_first_partial_emitted: bool = False
        self._turn_final_emitted: bool = False

        # Deepgram feature toggles
        self._desired_vad_state: bool = True
        self._vad_events_enabled: bool = True

    def _diag_payload(self, **extra: Any) -> dict:
        payload: dict[str, Any] = {"provider": "deepgram"}
        if self._diag_session_id:
            payload["session_id"] = self._diag_session_id
        tag = self._url_tag or self._diag_tag_hint
        if tag:
            payload["tag"] = tag
        payload.update(extra)
        provider = payload.get("provider")
        evidence = {"provider": provider} if provider else None
        try:
            payload.update(make_source_meta("asr_provider", evidence=evidence))
        except Exception:
            pass
        return payload

    def _emit_diag(self, label: str, **extra: Any) -> None:
        payload = self._diag_payload(**extra)
        hook = self._diag_hook
        if callable(hook):
            try:
                hook(label, **payload)
            except TypeError:
                try:
                    hook(label, payload)
                except Exception:
                    pass
            except Exception:
                pass
        try:
            _admin_emit("asr:diag", label=label, **dict(payload))
        except Exception:
            pass

    def _emit_transition_event(
        self,
        event: str,
        *,
        code: Optional[object] = None,
        path: Optional[object] = None,
    ) -> None:
        sid = self._diag_session_id
        if not sid or not event:
            return
        meta: dict[str, object] = {}
        if code is not None:
            try:
                meta["code"] = str(code)
            except Exception:
                meta["code"] = "unknown"
        if path is not None:
            try:
                meta["path"] = str(path)
            except Exception:
                meta["path"] = "unknown"
        meta.setdefault("component", "server_asr")
        try:
            flow_emit(
                session_id=sid,
                level="transition",
                phase="asr",
                type=event,
                who="system",
                meta=meta or None,
            )
        except Exception:
            pass

    def _record_error(self, code: Optional[object]) -> None:
        self._recover_pending = True
        self._emit_transition_event("asr_error", code=code)

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

    async def _signal_ready(self, *, backend_ready: bool = False) -> None:
        self._ready_observed = True
        if self._recover_pending:
            self._emit_transition_event("recover_ok", path="asr")
            self._recover_pending = False
        if not self._open_evt.is_set():
            self._open_evt.set()
        if not self._asr_open_emitted:
            self._asr_open_emitted = True
            try:
                await self._ev_queue.put({"type": "asr_open"})
            except Exception:
                pass
        if backend_ready:
            self._backend_ready_observed = True
            if not self._asr_ready_emitted:
                try:
                    await self._emit_flow_event("asr_ready")
                finally:
                    self._asr_ready_emitted = True
        await self._maybe_emit_asr_start()
        # schedule a flush shortly after ASR open (lets DG finish configure)
        self._schedule_flush(delay=0.0)

    async def _maybe_emit_asr_start(self) -> None:
        if self._asr_start_emitted:
            return
        if not self._ready_observed or not self._first_real_sent:
            return
        self._asr_start_emitted = True
        self._emit_diag("asr_open", active=True)

    async def _apply_vad_state(self) -> None:
        desired = bool(self._desired_vad_state)
        if self._vad_events_enabled == desired:
            return
        ws = self._ws
        if not ws or not self._ws_is_open(ws):
            return
        sid = self._sid_for_log()
        payload = {"type": "Configure", "features": {"vad_events": desired}}
        try:
            async with self._send_lock:
                await ws.send(json.dumps(payload))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - network safeguards
            self._logger.warning(
                "Deepgram vad configure failed sid=%s enabled=%s err=%s",
                sid,
                desired,
                exc,
            )
            if callable(self._jlog):
                try:
                    self._jlog(
                        "dg_vad_config_error",
                        sid=sid,
                        dg_id=self._dg_id,
                        enabled=desired,
                        err=exc.__class__.__name__,
                    )
                except Exception:
                    pass
        else:
            self._vad_events_enabled = desired
            if callable(self._jlog):
                try:
                    self._jlog(
                        "dg_vad_config",
                        sid=sid,
                        dg_id=self._dg_id,
                        enabled=desired,
                    )
                except Exception:
                    pass

    async def _publish_provider_event(self, event: str, payload: Any) -> None:
        data: dict[str, Any] = {"type": "provider_event", "event": event or "unknown"}
        if payload is not None:
            data["payload"] = payload
        try:
            await self._ev_queue.put(data)
        except Exception:
            pass

    async def set_vad_events_enabled(self, enabled: bool) -> None:
        """Request Deepgram to enable or disable vad_events."""
        self._desired_vad_state = bool(enabled)
        if not self.is_open():
            return
        await self._apply_vad_state()

    async def _emit_flow_event(
        self, event: str, meta: Optional[dict[str, Any]] = None
    ) -> None:
        if not event:
            return
        frame: dict[str, Any] = {"type": event}
        if meta is not None:
            frame["meta"] = meta
        try:
            await self._ev_queue.put(frame)
        except Exception:
            pass

    def _cfg_value(self, key: str) -> Optional[str]:
        cfg: dict[str, Any]
        if isinstance(self._cfg, dict):
            cfg = self._cfg
        else:
            cfg = {}
        for variant in (key, f"dg_{key}"):
            try:
                val = cfg.get(variant)
            except Exception:
                val = None
            if val not in (None, ""):
                try:
                    return str(val)
                except Exception:
                    continue
        nested = cfg.get("deepgram") if isinstance(cfg.get("deepgram"), dict) else None
        if isinstance(nested, dict):
            try:
                val = nested.get(key)
            except Exception:
                val = None
            if val not in (None, ""):
                try:
                    return str(val)
                except Exception:
                    pass
        env_val = os.getenv(f"DEEPGRAM_{key.upper()}", "").strip()
        return env_val or None

    def _ensure_turn_started(self, ts: Optional[float] = None) -> None:
        if self._turn_active:
            return
        self._turn_active = True
        self._turn_counter += 1
        self._turn_current_id = self._turn_counter
        now = ts or time.time()
        self._turn_start_ts = now
        self._turn_first_partial_ts = 0.0
        self._turn_final_ts = 0.0
        self._turn_bytes_baseline = self._bytes_forwarded
        self._turn_metrics_recorded = False
        self._turn_saw_1011 = False
        self._turn_last_close_code = None
        self._turn_first_partial_emitted = False
        self._turn_final_emitted = False

    def _note_first_partial(self, ts: Optional[float] = None) -> bool:
        if not self._turn_active:
            self._ensure_turn_started(ts)
        if self._turn_first_partial_ts <= 0.0:
            self._turn_first_partial_ts = ts or time.time()
            return True
        return False

    def _note_final_observed(self, ts: Optional[float] = None) -> None:
        if not self._turn_active:
            self._ensure_turn_started(ts)
        self._turn_final_ts = ts or time.time()

    def _emit_turn_metrics(self) -> None:
        if self._turn_metrics_recorded:
            return
        turn_id = self._turn_current_id
        if turn_id is None:
            return
        final_ts = self._turn_final_ts or time.time()
        start_ts = self._turn_start_ts
        first_partial_ms: Optional[int] = None
        if start_ts and self._turn_first_partial_ts:
            first_partial_ms = int(max(0.0, (self._turn_first_partial_ts - start_ts) * 1000))
        final_ms: Optional[int] = None
        if start_ts:
            final_ms = int(max(0.0, (final_ts - start_ts) * 1000))
        bytes_forwarded = max(0, self._bytes_forwarded - self._turn_bytes_baseline)
        session_id = self._diag_session_id or None
        if not session_id:
            sid_candidate = self._sid_for_log()
            if sid_candidate and sid_candidate != "?":
                session_id = sid_candidate
        payload: dict[str, Any] = {
            "session_id": session_id,
            "dg_model": self._cfg_value("model"),
            "dg_tier": self._cfg_value("tier"),
            "first_partial_ms": first_partial_ms,
            "final_ms": final_ms,
            "dg_1011": bool(self._turn_saw_1011),
            "bytes_forwarded": bytes_forwarded,
        }
        clean_payload = {k: v for k, v in payload.items() if v is not None}
        record_turn_metrics(turn_id, clean_payload)
        sid_for_bus = session_id or self._sid_for_log()
        if sid_for_bus and sid_for_bus != "?":
            frame = {"type": "asr.metrics", "turn_id": turn_id}
            frame.update(clean_payload)
            try:
                stream_bus.broadcast(str(sid_for_bus), frame)
            except Exception:
                pass
        self._turn_metrics_recorded = True
        self._turn_active = False
        self._turn_current_id = None
        self._turn_start_ts = 0.0
        self._turn_first_partial_ts = 0.0
        self._turn_final_ts = 0.0
        self._turn_bytes_baseline = self._bytes_forwarded
        self._turn_saw_1011 = False
        self._turn_last_close_code = None
        self._turn_first_partial_emitted = False
        self._turn_final_emitted = False

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
                pass

        self._flush_task = asyncio.create_task(_runner())

    def _schedule_auto_close(self, reason: str) -> None:
        if self._closed or self._closing:
            return
        if self._auto_close_requested:
            return
        if self._auto_close_task and not self._auto_close_task.done():
            return

        self._auto_close_requested = True
        sid = self._sid_for_log()
        self._logger.info(
            "Deepgram auto close scheduled sid=%s reason=%s", sid, reason
        )
        if callable(self._jlog):
            try:
                self._jlog(
                    "dg_auto_close_on_final",
                    sid=sid,
                    dg_id=self._dg_id,
                    tag=self._url_tag,
                    reason=reason,
                    queued=len(self._tx_queue),
                )
            except Exception:
                pass
        self._emit_diag("auto_close_on_final", reason=reason, active=True)

        try:
            self._auto_close_task = asyncio.create_task(
                self.close(wait_for_final=False, linger_ms=0)
            )
        except Exception:
            self._auto_close_task = None
            self._auto_close_requested = False

    async def _flush_tx(self) -> Tuple[int, Optional[str]]:
        """Drain queued audio if the socket is open and we've signaled ready."""
        sid = self._sid_for_log()
        queued_at_start = len(self._tx_queue)
        ws_open_flag = self._ws_is_open()
        open_evt_set = self._open_evt.is_set()

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
                self._tx_queue.popleft()
                continue
            try:
                send_ts = time.time()
                self._ensure_turn_started(send_ts)
                async with self._send_lock:
                    await ws.send(data)
                self._first_real_sent = True
                self._bytes_forwarded += len(data)
                self._last_chunk_ts = time.time()
                self._tx_queue.popleft()
                total_sent += len(data)
                sent_chunks += 1
                if first_chunk is None:
                    first_chunk = data
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
                await self._maybe_emit_asr_start()
            except Exception:
                # Transient send issue; stop and retry on next trigger
                pass
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
            return

        self._asr_open_emitted = False
        self._asr_start_emitted = False
        self._asr_connect_emitted = False
        self._asr_ready_emitted = False
        self._backend_ready_observed = False
        self._ready_observed = False
        self._first_real_sent = False
        self._bytes_forwarded = 0
        self._turn_active = False
        self._turn_current_id = None
        self._turn_start_ts = 0.0
        self._turn_first_partial_ts = 0.0
        self._turn_final_ts = 0.0
        self._turn_bytes_baseline = 0
        self._turn_metrics_recorded = False
        self._turn_saw_1011 = False
        self._turn_last_close_code = None
        self._turn_first_partial_emitted = False
        self._turn_final_emitted = False
        try:
            self._open_evt.clear()
        except Exception:
            self._open_evt = asyncio.Event()
        try:
            self._final_event.clear()
        except Exception:
            self._final_event = asyncio.Event()

        url = _dg_url(self._cfg)
        self._emit_diag("provider_open", active=True, url=url)

        transport = (self._cfg or {}).get("_transport", {}) or {}
        containerized = bool(transport.get("containerized_opus"))
        safe_url = url
        url_meta: dict[str, Any] = {}
        connect_meta: dict[str, Any] = {
            "containerized": containerized,
            "url_has_encoding": False,
            "url_has_samplerate": False,
            "url_has_channels": False,
        }
        q: dict[str, Any] = {}

        try:
            # Diagnostic: parse params and emit compact JSON log that shows
            # whether we omitted encoding/sample_rate/channels (containerized) or sent raw.
            import urllib.parse as _p

            parts = _p.urlsplit(url)
            q = dict(_p.parse_qsl(parts.query, keep_blank_values=True))
            self._url_tag = q.get("tag") or None
            url_meta = {
                "container": transport.get("container"),
                "codec": transport.get("codec"),
                "containerized_opus": containerized,
                "normalized_pcm": bool(transport.get("normalized_pcm")),
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
            connect_meta["url_has_encoding"] = "encoding" in q
            connect_meta["url_has_samplerate"] = "sample_rate" in q
            connect_meta["url_has_channels"] = "channels" in q

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
            safe_url = f"{parts.scheme}://{parts.netloc}{parts.path}"
            if sanitized_query:
                safe_url = f"{safe_url}?{sanitized_query}"
            if parts.fragment:
                safe_url = f"{safe_url}#{parts.fragment}"
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
                        omitted_params=url_meta.get("omitted_params"),
                        raw_params=url_meta.get("raw_params"),
                    )
                except Exception:
                    pass

            # Keep existing human-readable info log
            self._logger.info(
                "dg_ws_connect sid=%s url=%s containerized_opus=%s normalized_pcm=%s sent_encoding=%s sent_sample_rate=%s sent_channels=%s",
                sid,
                url,
                containerized,
                url_meta.get("normalized_pcm"),
                q.get("encoding"),
                q.get("sample_rate"),
                q.get("channels"),
            )
        except Exception:
            self._logger.info("Deepgram connect start sid=%s url=%s", sid, url)

        if not self._asr_connect_emitted:
            await self._emit_flow_event("asr_connect", connect_meta)
            self._asr_connect_emitted = True

        key_value, key_len = _api_key_info(
            log=self._advanced_logging_enabled, logger_obj=self._logger
        )
        ws_headers = [("Authorization", f"Token {key_value}")]
        del key_value
        subprotocols = None
        self._logger.info(
            "Deepgram connect attempt sid=%s safe_url=%s key_len=%s subprotocols=%s",
            sid,
            safe_url,
            key_len,
            subprotocols,
        )
        if callable(self._jlog):
            try:
                self._jlog(
                    "dg_connect_attempt",
                    sid=sid,
                    dg_id=self._dg_id,
                    safe_url=safe_url,
                    key_len=key_len,
                    subprotocols=subprotocols,
                )
            except Exception:
                pass

        start_ts = time.time()
        elapsed_ms = 0
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
                features = cfg_payload.get("features")
                if isinstance(features, dict):
                    self._vad_events_enabled = bool(features.get("vad_events", True))
                else:
                    self._vad_events_enabled = True
                if self._desired_vad_state != self._vad_events_enabled:
                    self._vad_events_enabled = self._desired_vad_state
                self._open_evt.set()
                await self._signal_ready(backend_ready=True)
                self._logger.info("Deepgram test-mode connect sid=%s", sid)
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
                try:
                    _admin_emit(
                        "dg_connect",
                        session_id=sid,
                        dg_id=self._dg_id,
                        safe_url=safe_url,
                        containerized=containerized,
                        tag=self._url_tag,
                        normalized_pcm=bool(url_meta.get("normalized_pcm")),
                        elapsed_ms=elapsed_ms,
                        test_mode=True,
                    )
                except Exception:
                    pass
                return

            try:
                self._ws = await websockets.connect(
                    url,
                    additional_headers=ws_headers,
                    max_size=None,
                )
            except TypeError:
                self._ws = await websockets.connect(
                    url,
                    extra_headers=ws_headers,
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
            features = cfg_payload.get("features")
            if isinstance(features, dict):
                self._vad_events_enabled = bool(features.get("vad_events", True))
            else:
                self._vad_events_enabled = True
            if self._desired_vad_state != self._vad_events_enabled:
                try:
                    await self._apply_vad_state()
                except Exception:
                    pass
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

            self._logger.info("Deepgram connect ok sid=%s", sid)
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
            try:
                _admin_emit(
                    "dg_connect",
                    session_id=sid,
                    dg_id=self._dg_id,
                    safe_url=safe_url,
                    containerized=containerized,
                    tag=self._url_tag,
                    normalized_pcm=bool(url_meta.get("normalized_pcm")),
                    elapsed_ms=elapsed_ms,
                    test_mode=False,
                )
            except Exception:
                pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            diag_extra: dict[str, Any] = {}
            header_items: list[tuple[str, str]] | None = None
            status_code: Any = None
            status_reason: Any = None

            if _INVALID_STATUS_TYPES and isinstance(exc, _INVALID_STATUS_TYPES):
                response = getattr(exc, "response", None)
                headers_obj = None
                if response is not None:
                    status_code = getattr(response, "status_code", None)
                    status_reason = getattr(response, "reason_phrase", None)
                    headers_obj = getattr(response, "headers", None)
                else:
                    status_code = getattr(exc, "status_code", None)
                    status_reason = getattr(exc, "reason_phrase", None)
                    headers_obj = getattr(exc, "headers", None)

                if headers_obj is not None:
                    header_iter = None
                    try:
                        header_iter = list(headers_obj.items())
                    except Exception:
                        try:
                            header_iter = list(headers_obj.raw_items())  # type: ignore[attr-defined]
                        except Exception:
                            header_iter = None
                    if header_iter is not None:
                        header_items = [(str(k), str(v)) for k, v in header_iter]

                if status_code is not None:
                    try:
                        diag_extra["http_status"] = int(status_code)
                    except Exception:
                        diag_extra["http_status"] = status_code
                if status_reason:
                    diag_extra["http_reason"] = str(status_reason)
                if header_items is not None:
                    diag_extra["http_headers"] = header_items

                self._logger.warning(
                    "Deepgram connect invalid status sid=%s status=%s reason=%s headers=%s url=%s",
                    sid,
                    diag_extra.get("http_status"),
                    diag_extra.get("http_reason"),
                    header_items,
                    safe_url,
                )
                if callable(self._jlog):
                    try:
                        self._jlog(
                            "dg_connect_invalid_status",
                            sid=sid,
                            dg_id=self._dg_id,
                            status=diag_extra.get("http_status"),
                            reason=diag_extra.get("http_reason"),
                            headers=header_items,
                            url=safe_url,
                        )
                    except Exception:
                        pass

            if callable(self._jlog):
                try:
                    elapsed_ms = int((time.time() - start_ts) * 1000)
                except Exception:
                    elapsed_ms = 0
                payload = {
                    "sid": sid,
                    "dg_id": self._dg_id,
                    "elapsed_ms": elapsed_ms,
                    "code": exc.__class__.__name__,
                }
                for key, value in diag_extra.items():
                    if value is not None:
                        payload[key] = value
                try:
                    self._jlog("asr_connect_err", **payload)
                except Exception:
                    pass

            diag_payload = {
                key: value for key, value in diag_extra.items() if value is not None
            }
            self._emit_diag(
                "asr_error",
                error=f"connect:{exc.__class__.__name__}",
                detail=_clip_text(str(exc), 200),
                url=url,
                **diag_payload,
            )
            self._record_error(f"connect:{exc.__class__.__name__}")
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
        current_task = asyncio.current_task()
        if self._auto_close_task and self._auto_close_task is not current_task:
            if not self._auto_close_task.done():
                try:
                    self._auto_close_task.cancel()
                    await self._auto_close_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
        self._auto_close_task = None
        self._auto_close_requested = False
        self._logger.info(
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
            self._logger.warning(
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
            self._logger.info(
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
                    self._record_error(err_txt)
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

            self._logger.info("Deepgram close complete sid=%s", sid)
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
                        bytes_forwarded=self._bytes_forwarded,
                        no_audio=self._bytes_forwarded == 0,
                    )
                except Exception:
                    pass

            ack_payload = {
                "status": "ok" if not drain_failed else "drain_timeout",
                "dg_id": self._dg_id,
                "wait_for_final": wait_for_final,
                "linger_ms": linger_ms,
                "had_result": self._had_result(),
                "drain_failed": drain_failed,
                "queued_chunks": dropped_chunks,
                "queued_bytes": dropped_bytes,
                "drain_wait_timeout": drain_wait_timeout,
                "bytes_forwarded": self._bytes_forwarded,
                "no_audio": self._bytes_forwarded == 0,
            }
            self._emit_diag("CloseStream ack", **ack_payload)

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
            self._record_error("drain_timeout")
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
                    self._logger.warning(
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

                if not isinstance(msg, dict):
                    await self._publish_provider_event("invalid_payload", msg)
                    continue

                raw_type = msg.get("type")
                evt_type = ""
                evt_name = "unknown"
                if isinstance(raw_type, str):
                    evt_name = raw_type
                    evt_type = raw_type.lower()
                elif raw_type is not None:
                    try:
                        evt_name = str(raw_type)
                        evt_type = evt_name.lower()
                    except Exception:
                        evt_type = ""

                if evt_type in ("metadata", "listening", "connected", "ready"):
                    await self._signal_ready(backend_ready=True)
                    await self._publish_provider_event(evt_type or evt_name, msg)
                    continue

                if evt_type in (
                    "results",
                    "transcript",
                    "partialtranscript",
                    "speech.update",
                ):
                    await self._signal_ready(backend_ready=True)
                    await self._publish_provider_event(evt_type or evt_name, msg)
                    text = ""
                    is_final = False

                    # Prefer channel.* first (typical DG shape)
                    channel = msg.get("channel") or {}
                    alts = channel.get("alternatives")
                    confidence = None
                    token_count = 0
                    speech_ms_val: Optional[int] = None
                    speech_container: Any = None
                    if isinstance(alts, list) and alts:
                        first_alt = alts[0]
                        text = (first_alt.get("transcript") or "").strip()
                        confidence = _safe_float(first_alt.get("confidence"))
                        words = first_alt.get("words")
                        if isinstance(words, list):
                            token_count = len(
                                [
                                    w
                                    for w in words
                                    if isinstance(w, dict)
                                    and (w.get("word") or "").strip()
                                ]
                            )
                        speech_container = first_alt.get("speech")

                    # Fallback: top-level alternatives (some messages)
                    if not text:
                        top_alts = msg.get("alternatives")
                        if isinstance(top_alts, list) and top_alts:
                            first_alt = top_alts[0]
                            text = (first_alt.get("transcript") or "").strip()
                            if confidence is None:
                                confidence = _safe_float(first_alt.get("confidence"))
                            if not token_count:
                                words = first_alt.get("words")
                                if isinstance(words, list):
                                    token_count = len(
                                        [
                                            w
                                            for w in words
                                            if isinstance(w, dict)
                                            and (w.get("word") or "").strip()
                                        ]
                                    )
                            if not speech_container:
                                speech_container = first_alt.get("speech")

                    # Fallback: top-level transcript (some messages)
                    if not text and isinstance(msg.get("transcript"), str):
                        text = (msg.get("transcript") or "").strip()

                    if confidence is None:
                        confidence = _safe_float(
                            channel.get("confidence") or msg.get("confidence")
                        )

                    if not token_count and text:
                        token_count = len([tok for tok in text.split() if tok])

                    if not isinstance(speech_container, dict):
                        alt_speech = channel.get("speech") if isinstance(channel, dict) else None
                        if isinstance(alt_speech, dict):
                            speech_container = alt_speech
                    if not isinstance(speech_container, dict):
                        top_level_speech = msg.get("speech")
                        if isinstance(top_level_speech, dict):
                            speech_container = top_level_speech
                        elif isinstance(top_level_speech, list) and top_level_speech:
                            speech_container = top_level_speech[-1]
                    speech_ms_val = _coerce_speech_ms(speech_container)

                    # Finalness can be on channel or top-level, or implied by event type
                    is_final = (
                        bool(channel.get("is_final"))
                        or bool(msg.get("is_final"))
                        or bool(msg.get("speech_final"))
                        or (evt_type in ("utteranceend", "UtteranceEnd"))
                    )

                    final_reason = None
                    final_reason_detail = None
                    if is_final:
                        speech = msg.get("speech")
                        endpointing = None
                        if isinstance(speech, dict):
                            endpointing = speech.get("endpointing")
                        if isinstance(endpointing, dict):
                            final_reason_detail = endpointing.get("type")
                        for key in ("reason", "end_reason", "speech_final_reason"):
                            val = msg.get(key)
                            if isinstance(val, str) and val:
                                final_reason_detail = val
                                break
                        if evt_type == "utteranceend" or msg.get("utterance_end"):
                            final_reason = "utterance_end"
                        if not final_reason and isinstance(final_reason_detail, str):
                            lowered = final_reason_detail.strip().lower()
                            if "utterance" in lowered or "endpoint" in lowered:
                                final_reason = "utterance_end"
                        if self._closing and not final_reason:
                            final_reason = "close_stream"
                        if not final_reason:
                            final_reason = "provider_final"

                    # No usable text in this message; keep listening
                    if text:
                        self._last_transcript = text
                    elif is_final:
                        text = self._last_transcript or ""
                    else:
                        continue

                    now_ts = time.time()
                    first_partial = self._note_first_partial(now_ts)
                    if is_final:
                        self._note_final_observed(now_ts)
                    if first_partial and not self._turn_first_partial_emitted:
                        self._turn_first_partial_emitted = True
                        try:
                            await self._emit_flow_event(
                                "asr_partial_first", meta={"conf": confidence}
                            )
                        except Exception:
                            pass

                    self._emit_diag(
                        "asr_final" if is_final else "asr_partial",
                        text=text,
                        chars=len(text),
                        active=True,
                    )
                    try:
                        payload = {
                            "type": "user_final" if is_final else "user_partial",
                            "text": text,
                        }
                        if is_final:
                            payload["final_reason"] = final_reason
                            if final_reason_detail:
                                payload["final_reason_detail"] = final_reason_detail
                        if confidence is not None:
                            payload["confidence"] = confidence
                        if token_count:
                            payload["token_count"] = token_count
                        if speech_ms_val is not None:
                            payload["speech_ms"] = speech_ms_val
                        await self._ev_queue.put(payload)
                    except Exception:
                        pass

                    if is_final:
                        if not self._turn_final_emitted:
                            self._turn_final_emitted = True
                            turn_id = self._turn_current_id
                            if turn_id is None:
                                turn_id = self._turn_counter
                            try:
                                await self._emit_flow_event(
                                    "asr_final",
                                    meta={
                                        "len": len(text),
                                        "conf": confidence,
                                        "turn_id": turn_id,
                                    },
                                )
                            except Exception:
                                pass
                        self._schedule_auto_close(final_reason or "provider_final")
                        self._any_result = True
                        self._final_event.set()
                        self._emit_turn_metrics()
                        self._last_transcript = ""
                        continue

                elif evt_type in ("error", "close"):
                    await self._publish_provider_event(evt_type or evt_name, msg)
                    self._logger.warning(
                        "Deepgram error event sid=%s evt_type=%s detail=%s",
                        sid,
                        evt_type,
                        _clip_text(str(msg), 200),
                    )
                    err_code = msg.get("error") or evt_type
                    self._emit_diag(
                        "asr_error",
                        error=err_code,
                        provider_error=True,
                        detail=_clip_text(str(msg), 200),
                    )
                    self._record_error(err_code)
                    try:
                        await self._ev_queue.put(
                            {"type": "asr_error", "error": err_code}
                        )
                    except Exception:
                        pass

                else:
                    await self._publish_provider_event(evt_type or evt_name, msg)
                    continue

        except asyncio.CancelledError:
            return
        except websockets.ConnectionClosed as e:
            code = getattr(e, "code", None)
            reason_raw = getattr(e, "reason", "")
            try:
                reason_txt = str(reason_raw or "")
            except Exception:
                reason_txt = ""
            if isinstance(code, int):
                self._turn_last_close_code = code
            else:
                self._turn_last_close_code = None
            if (
                code == 1011
                and self._turn_current_id is not None
                and not self._turn_metrics_recorded
            ):
                self._turn_saw_1011 = True
                if self._turn_final_ts <= 0.0:
                    self._turn_final_ts = time.time()
                self._emit_turn_metrics()
            timeout_flag = False
            try:
                timeout_flag = bool(reason_txt) and "timeout" in reason_txt.lower()
            except Exception:
                timeout_flag = False
            log_kwargs = {
                "sid": sid,
                "code": code,
                "reason": reason_txt,
                "had_result": self._had_result(),
            }
            log_fn = self._logger.warning
            if timeout_flag or code == 1011:
                log_fn = self._logger.info
            log_fn(
                "Deepgram websocket closed sid=%(sid)s code=%(code)s reason=%(reason)s had_result=%(had_result)s",
                log_kwargs,
            )
            await self._publish_provider_event(
                "connection_closed",
                {"code": code, "reason": reason_txt, "timeout": timeout_flag},
            )
            if timeout_flag or code == 1011:
                self._emit_diag(
                    "asr_timeout",
                    provider_error=False,
                    code=code,
                    reason=reason_txt,
                    active=False,
                )
                self._final_event.set()
                return
            if not self._had_result():
                err_txt = f"recv_closed:{code}:{reason_txt}"
                self._emit_diag(
                    "asr_error",
                    error=err_txt,
                    provider_error=True,
                    code=code,
                    reason=reason_txt,
                )
                self._record_error(err_txt)
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
            self._logger.exception("Deepgram rx loop error sid=%s", sid)
            self._emit_diag(
                "asr_error",
                error=f"rx:{e.__class__.__name__}",
                provider_error=True,
            )
            self._record_error(f"rx:{e.__class__.__name__}")
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
                except websockets.ConnectionClosed as exc:
                    self._logger.warning("Deepgram keepalive closed sid=%s err=%s", sid, exc)
                    break
                except Exception as exc:
                    self._logger.warning("Deepgram keepalive failed sid=%s err=%s", sid, exc)
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
