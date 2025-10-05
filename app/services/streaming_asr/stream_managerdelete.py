import asyncio
import json
import logging
import os as _os
import time
from typing import Callable, Dict, Optional

import websockets  # Deepgram realtime client

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Admin SSE emitter (safe no-op if Admin log pipe not present)

EmitFn = Callable[[str, str], None]


def _mk_emit(emit: Optional[Callable[..., None]] = None) -> Callable[..., None]:
    """
    Wrap the provided emitter (or import app.admin_log.emit) so that:
    - Missing emitter is a no-op.
    - Old signatures (emit(kind, label)) still work.
    - Extra fields are passed as kwargs.
    """
    if emit:

        def _wrapped(kind: str, label: str, extra: Dict = None):
            try:
                emit(kind, label=label, **(extra or {}))
            except TypeError:
                try:
                    emit(kind, label)
                except Exception:
                    pass
            except Exception:
                pass

        return _wrapped

    # Fall back to app.admin_log.emit if available
    try:
        from app.admin_log import emit as admin_emit  # type: ignore
    except Exception:  # pragma: no cover
        admin_emit = None

    if not admin_emit:

        def _no(*_a, **_k):
            return None

        return _no

    def _wrapped(kind: str, label: str, extra: Dict = None):
        try:
            admin_emit(kind, label=label, **(extra or {}))
        except TypeError:
            try:
                admin_emit(kind, label)
            except Exception:
                pass
        except Exception:
            pass

    return _wrapped


# -----------------------------------------------------------------------------
# Background asyncio loop (single thread) so all ASR work shares one loop.
# This prevents: "Future ... attached to a different loop".

import threading


class _BGLoop(threading.Thread):
    _inst: Optional["_BGLoop"] = None
    _lock = threading.Lock()

    def __init__(self):
        super().__init__(name="asr-bg-loop", daemon=True)
        self._ready = threading.Event()
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    def run(self):
        try:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self._ready.set()
            self.loop.run_forever()
        finally:
            try:
                self.loop.close()
            except Exception:
                pass

    @classmethod
    def get(cls) -> "_BGLoop":
        with cls._lock:
            if cls._inst is None:
                cls._inst = _BGLoop()
                cls._inst.start()
                cls._inst._ready.wait(5)
            return cls._inst


def _submit_bg(coro, *, timeout: Optional[float] = None):
    loop = _BGLoop.get().loop
    if loop is None:
        raise RuntimeError("ASR background loop not ready")
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    return fut if timeout is None else fut.result(timeout=timeout or 10)


# -----------------------------------------------------------------------------
# Deepgram stream manager

_streams: Dict[str, "DeepgramStreamManager"] = {}
_streams_lock = threading.Lock()  # ensure single manager per session_id


class DeepgramStreamManager:
    """
    Minimal, production-safe Deepgram streaming client.

    Key behavior:
      • Drops the tiny first chunk (<64 B) before sending (common capture preamble).
      • Sends {"type":"CloseStream"} before closing.
      • Lingers briefly after last chunk (~600 ms) and waits up to 8 s for a final.
      • Emits precise labels for Admin SSE and cleans up per-session.
    """

    DG_URL = (
        "wss://api.deepgram.com/v1/listen"
        "?encoding=opus"
        "&sample_rate=48000"
        "&channels=1"
        "&interim_results=true"
        "&smart_format=true"
        "&punctuate=true"
        "&vad_events=true"
        "&utterance_end_ms=1200"
    )

    def __init__(
        self,
        api_key: str,
        session_id: str,
        emit: Optional[Callable[..., None]] = None,
        *,
        min_valid_bytes: int = 64,
        linger_after_last_chunk_ms: int = 600,
        final_wait_s: float = 8.0,
        connect_timeout_s: float = 10.0,
        recv_max_wait_s: float = 30.0,
    ):
        self.api_key = api_key
        self.session_id = session_id
        self.emit = _mk_emit(emit)

        self.min_valid_bytes = int(min_valid_bytes)
        self.linger_ms = int(linger_after_last_chunk_ms)
        self.final_wait_s = float(final_wait_s)
        self.connect_timeout_s = float(connect_timeout_s)
        self.recv_max_wait_s = float(recv_max_wait_s)

        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self._opened = False
        self._closing = False
        self._first_real_chunk_sent = False
        self._last_chunk_ts = 0.0

        self._recv_task: Optional[asyncio.Task] = None
        self._final_event = asyncio.Event()
        self._any_result = False

    # -- lifecycle -------------------------------------------------------------

    async def open(self):
        if self._opened:
            return
        logger.info("DeepgramStreamManager open session_id=%s", self.session_id)
        headers = [("Authorization", f"Token {self.api_key}")]
        self.emit(
            "asr",
            "provider_open",
            {"provider": "deepgram", "session_id": self.session_id, "url": self.DG_URL},
        )
        try:
            try:
                # websockets ≥ 11
                self.ws = await asyncio.wait_for(
                    websockets.connect(
                        self.DG_URL, additional_headers=headers, max_size=None
                    ),
                    timeout=self.connect_timeout_s,
                )
            except TypeError:
                # older versions use extra_headers
                self.ws = await asyncio.wait_for(
                    websockets.connect(
                        self.DG_URL, extra_headers=headers, max_size=None
                    ),
                    timeout=self.connect_timeout_s,
                )
            self._opened = True
            self.emit("asr", "asr_open", {"session_id": self.session_id})
            # Start receiver
            self._recv_task = asyncio.create_task(
                self._recv_loop(), name=f"dg-recv-{self.session_id}"
            )
            logger.info("DeepgramStreamManager open ok session_id=%s", self.session_id)
        except Exception as e:
            self.emit(
                "asr",
                "asr_error",
                {"session_id": self.session_id, "error": f"open_failed:{type(e).__name__}:{e}"},
            )
            logger.exception(
                "DeepgramStreamManager open failed session_id=%s",
                self.session_id,
            )
            raise

    async def send_chunk(self, data: bytes, seq: Optional[int] = None):
        """
        Forward one mic frame to Deepgram. Drops a tiny preamble first chunk.
        Emits voice:chunk for Admin SSE.
        """
        if not data:
            return

        if not self._first_real_chunk_sent and len(data) < self.min_valid_bytes:
            logger.debug(
                "DeepgramStreamManager drop small chunk session_id=%s bytes=%s",
                self.session_id,
                len(data),
            )
            self.emit(
                "asr",
                "drop_small_first_chunk",
                {"session_id": self.session_id, "bytes": len(data)},
            )
            return

        self._first_real_chunk_sent = True

        if self.ws is None or not self._opened:
            logger.warning(
                "DeepgramStreamManager send before open session_id=%s",
                self.session_id,
            )
            self.emit(
                "asr",
                "asr_error",
                {"session_id": self.session_id, "error": "send_before_open"},
            )
            return

        try:
            await self.ws.send(data)
            self._last_chunk_ts = time.time()
            logger.debug(
                "DeepgramStreamManager sent chunk session_id=%s bytes=%s seq=%s",
                self.session_id,
                len(data),
                seq,
            )
            self.emit(
                "voice",
                "voice:chunk",
                {
                    "session_id": self.session_id,
                    "seq": (int(seq) if seq is not None else -1),
                    "bytes": len(data),
                },
            )
        except Exception as e:
            self.emit(
                "asr",
                "asr_error",
                {"session_id": self.session_id, "error": f"send_failed:{type(e).__name__}:{e}"},
            )
            raise

    async def _recv_loop(self):
        """Read Deepgram frames; detect partial/final and notify Admin SSE."""
        try:
            self._any_result = False
            while self.ws and not self._closing:
                try:
                    msg = await asyncio.wait_for(self.ws.recv(), timeout=self.recv_max_wait_s)
                except asyncio.TimeoutError:
                    continue

                try:
                    if isinstance(msg, (bytes, bytearray)):
                        try:
                            msg = msg.decode("utf-8", "ignore")
                        except Exception:
                            continue
                    j = json.loads(msg)
                except Exception:
                    continue

                # Heuristic: mark partial/final based on DG schema
                is_final = False
                try:
                    if isinstance(j, dict):
                        if j.get("type") in ("Results", "results", "transcript"):
                            ch = j.get("channel") or {}
                            if isinstance(ch, dict):
                                is_final = bool(ch.get("is_final"))
                            elif "is_final" in j:
                                is_final = bool(j.get("is_final"))
                        elif j.get("is_final") is True:
                            is_final = True
                except Exception:
                    is_final = False

                if is_final:
                    self._any_result = True
                    logger.info(
                        "DeepgramStreamManager final session_id=%s",
                        self.session_id,
                    )
                    self.emit("asr", "asr_final", {"session_id": self.session_id})
                    self._final_event.set()
                else:
                    self._any_result = True
                    logger.debug(
                        "DeepgramStreamManager partial session_id=%s",
                        self.session_id,
                    )
                    self.emit("asr", "asr_partial", {"session_id": self.session_id})

        except Exception as e:
            logger.exception(
                "DeepgramStreamManager recv loop error session_id=%s",
                self.session_id,
            )
            self.emit(
                "asr",
                "asr_error",
                {"session_id": self.session_id, "error": f"recv_loop_error:{type(e).__name__}:{e}"},
            )

    async def end(self, wait_for_final: bool = True):
        """
        Graceful shutdown:
          1) Linger briefly after the last chunk (some providers need a beat).
          2) Send {"type":"CloseStream"}.
          3) Optionally wait for a final result (bounded by final_wait_s).
        """
        if self._closing:
            return
        self._closing = True
        logger.info(
            "DeepgramStreamManager end session_id=%s wait_for_final=%s",
            self.session_id,
            wait_for_final,
        )
        try:
            # Linger
            await asyncio.sleep(self.linger_ms / 1000.0)

            # Close message (per DG examples)
            try:
                if self.ws:
                    await self.ws.send(json.dumps({"type": "CloseStream"}))
            except Exception:
                pass

            # Bounded wait for a final
            if wait_for_final:
                try:
                    await asyncio.wait_for(self._final_event.wait(), timeout=self.final_wait_s)
                except asyncio.TimeoutError:
                    if not self._any_result:
                        logger.warning(
                            "DeepgramStreamManager final timeout session_id=%s timeout=%s",
                            self.session_id,
                            self.final_wait_s,
                        )
                        self.emit(
                            "asr",
                            "asr_error",
                            {"session_id": self.session_id, "error": f"final_timeout:{self.final_wait_s:.1f}s"},
                        )
        finally:
            try:
                if self.ws:
                    await self.ws.close()
            except Exception:
                pass
            self._opened = False
            logger.info("DeepgramStreamManager closed session_id=%s", self.session_id)

    # -------------------------------------------------------------------------


def _get(
    *, api_key: str, session_id: str, emit: Optional[Callable[..., None]] = None
) -> "DeepgramStreamManager":
    with _streams_lock:
        mgr = _streams.get(session_id)
        if mgr is None:
            mgr = DeepgramStreamManager(api_key=api_key, session_id=session_id, emit=emit)
            _streams[session_id] = mgr
        return mgr


# Public async helpers used by the compat manager
async def asr_open(session_id: str, api_key: str, emit: Optional[Callable[..., None]] = None):
    mgr = _get(api_key=api_key, session_id=session_id, emit=emit)
    await mgr.open()


async def asr_send_chunk(session_id: str, data: bytes, seq: Optional[int] = None):
    with _streams_lock:
        mgr = _streams.get(session_id)
    if mgr is None:
        raise RuntimeError("asr_send_chunk called before asr_open")
    await mgr.send_chunk(data, seq=seq)


async def asr_end(session_id: str, wait_for_final: bool = True):
    with _streams_lock:
        mgr = _streams.get(session_id)
    if mgr is None:
        return
    try:
        await mgr.end(wait_for_final=wait_for_final)
    finally:
        with _streams_lock:
            _streams.pop(session_id, None)


# -----------------------------------------------------------------------------
# Compatibility shim for legacy callers (Flask views call enqueue/end)

class _CompatManager:
    """
    Back-compat manager exposing .enqueue(session_id, item) for legacy endpoints.
    Accepts either raw bytes or a dict {'data': bytes, 'chunk_seq': int, 'user_msg_id': str}.
    Uses a SINGLE background asyncio loop so futures are always bound to the same loop.
    Also serializes open() so only ONE Deepgram WS is created per session_id.
    """
    _STATS_IDLE_MAX_AGE = 900.0  # seconds before inactive sessions are pruned

    def __init__(self):
        self._opened = set()
        self._opening = set()
        self._lock = threading.Lock()
        self._stats: Dict[str, Dict[str, object]] = {}

    @staticmethod
    def _blank_stats() -> Dict[str, object]:
        return {
            "partials": 0,
            "finals": 0,
            "err": None,
            "err_count": 0,
            "provider_errors": 0,
            "active": False,
            "last_event": None,
        }

    def _ensure_stats_locked(
        self,
        session_id: str,
        *,
        reset: bool = False,
        active: Optional[bool] = None,
    ) -> Dict[str, object]:
        now = time.time()
        stats = self._stats.get(session_id)
        if stats is None:
            stats = self._blank_stats()
            self._stats[session_id] = stats
        if reset:
            stats.update(self._blank_stats())
        if active is not None:
            stats["active"] = bool(active)
        stats["_updated"] = now
        return stats

    def _prune_stats_locked(self):
        cutoff = time.time() - self._STATS_IDLE_MAX_AGE
        stale = [
            sid
            for sid, stats in self._stats.items()
            if not stats.get("active") and stats.get("_updated", 0.0) < cutoff
        ]
        for sid in stale:
            self._stats.pop(sid, None)

    @staticmethod
    def _public_stats(stats: Dict[str, object]) -> Dict[str, object]:
        pub = {k: stats.get(k) for k in ("partials", "finals", "err", "err_count", "provider_errors", "active", "last_event")}
        for key, default in _CompatManager._blank_stats().items():
            pub.setdefault(key, default)
        return pub

    def record_event(
        self,
        session_id: str,
        label: str,
        *,
        error: Optional[str] = None,
        active: Optional[bool] = None,
    ) -> None:
        """Record an event for a session in a thread-safe manner."""
        if not session_id:
            return

        reset = label in {"provider_open", "asr_open"}
        active_state: Optional[bool]
        if active is None:
            active_state = True
        else:
            active_state = bool(active)

        with self._lock:
            self._prune_stats_locked()
            stats = self._ensure_stats_locked(
                session_id,
                reset=reset,
                active=active_state,
            )
            stats["last_event"] = label

            if label == "asr_partial":
                stats["partials"] = int(stats.get("partials", 0)) + 1
            elif label == "asr_final":
                stats["finals"] = int(stats.get("finals", 0)) + 1
            elif label == "asr_error":
                stats["err"] = error
                stats["err_count"] = int(stats.get("err_count", 0)) + 1
                stats["provider_errors"] = int(stats.get("provider_errors", 0)) + 1

    def _api_key(self) -> str:
        key = _os.getenv("DEEPGRAM_API_KEY", "").strip()
        if not key:
            raise RuntimeError("DEEPGRAM_API_KEY not set")
        return key

    def _emit(self, kind: str, label: str, **extra):
        session_id = extra.get("session_id")
        if session_id:
            self.record_event(session_id, label, error=extra.get("error"))

        try:
            from app.admin_log import emit as admin_emit  # type: ignore
            admin_emit(kind, label=label, **(extra or {}))
        except Exception:
            pass

    def _ensure_open_sync(self, session_id: str):
        with self._lock:
            if session_id in self._opened or session_id in self._opening:
                return
            # mark as opening under lock so parallel threads don't double-open
            self._opening.add(session_id)
        ok = False
        try:
            _submit_bg(asr_open(session_id=session_id, api_key=self._api_key(), emit=self._emit), timeout=10)
            ok = True
        finally:
            with self._lock:
                self._opening.discard(session_id)
                if ok:
                    self._opened.add(session_id)

    def enqueue(self, session_id: str, item):
        # Normalize inputs
        seq = None
        data = b""
        if isinstance(item, (bytes, bytearray, memoryview)):
            data = bytes(item)
        elif isinstance(item, dict):
            data = item.get("data") or item.get("bytes") or b""
            try:
                seq = int(item.get("chunk_seq")) if item.get("chunk_seq") is not None else None
            except Exception:
                seq = None
        else:
            return

        # Always operate on the single background loop
        self._ensure_open_sync(session_id)
        try:
            _submit_bg(asr_send_chunk(session_id, data, seq=seq))
        except Exception as e:
            self._emit("asr", "asr_error", session_id=session_id, error=f"enqueue_failed:{type(e).__name__}:{e}")

    def end(self, session_id: str, wait_for_final: bool = True):
        self.record_event(session_id, "end", active=False)
        try:
            _submit_bg(asr_end(session_id, wait_for_final=wait_for_final), timeout=12)
        finally:
            with self._lock:
                self._opened.discard(session_id)

    def stats(self, session_id: str) -> Dict[str, object]:
        with self._lock:
            self._prune_stats_locked()
            stats = self._stats.get(session_id)
            if not stats:
                return self._blank_stats().copy()
            return self._public_stats(stats)

    def stats_all(self) -> Dict[str, object]:
        with self._lock:
            self._prune_stats_locked()
            sessions = {sid: self._public_stats(stats) for sid, stats in self._stats.items()}
        totals = {
            "partials": sum(int(s.get("partials", 0)) for s in sessions.values()),
            "finals": sum(int(s.get("finals", 0)) for s in sessions.values()),
            "err_count": sum(int(s.get("err_count", 0)) for s in sessions.values()),
            "provider_errors": sum(int(s.get("provider_errors", 0)) for s in sessions.values()),
            "err": None,
            "active": any(bool(s.get("active")) for s in sessions.values()),
            "last_event": None,
            "sessions": sessions,
        }
        return totals


_COMPAT_SINGLETON = _CompatManager()


def get_manager() -> _CompatManager:
    return _COMPAT_SINGLETON
