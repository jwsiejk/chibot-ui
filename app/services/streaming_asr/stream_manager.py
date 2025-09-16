import asyncio
import json
import logging
import time
from typing import Callable, Dict, Optional

import websockets  # uses your existing WebSocket client lib

logger = logging.getLogger(__name__)

# ---- Types ------------------------------------------------------------------

EmitFn = Callable[[str, str], None]
EmitDictFn = Callable[[str, str, dict], None]

# ---- Helpers ----------------------------------------------------------------

def _noop_emit(kind: str, label: str, extra: Optional[dict] = None):
    # Safe no-op if no SSE emitter is provided
    pass

def _mk_emit(emit: Optional[Callable[..., None]]):
    """
    Accepts:
      - emit(kind, label) or
      - emit(kind, label, **extra)
    Returns a normalized emitter fn.
    """
    if emit is None:
        return _noop_emit

    def _wrapped(kind: str, label: str, extra: Optional[dict] = None):
        try:
            if extra:
                emit(kind, label, **extra)
            else:
                emit(kind, label)
        except TypeError:
            # Older emitter signature without kwargs
            try:
                emit(kind, label)
            except Exception:
                pass
        except Exception:
            pass

    return _wrapped

# ---- Manager ----------------------------------------------------------------

class DeepgramStreamManager:
    """
    Minimal, production-safe Deepgram streaming client.

    Key behavior (aligns with Deepgram demo):
      • Drop the tiny first chunk (1–few bytes) used by some capture stacks.
      • Send explicit {"type":"CloseStream"} before closing.
      • Keep the socket open long enough to receive final results.
    """

    DG_URL = (
        "wss://api.deepgram.com/v1/listen"
        "?interim_results=true"
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
        min_valid_bytes: int = 64,
        final_wait_s: float = 8.0,
        linger_after_last_chunk_ms: int = 600,
        connect_timeout_s: float = 10.0,
        recv_max_wait_s: float = 30.0,
    ):
        self.api_key = api_key
        self.session_id = session_id
        self.emit = _mk_emit(emit)
        self.min_valid_bytes = int(min_valid_bytes)
        self.final_wait_s = float(final_wait_s)
        self.linger_ms = int(linger_after_last_chunk_ms)
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
        headers = [("Authorization", f"Token {self.api_key}")]
        self.emit("asr", "provider_open", {
            "provider": "deepgram",
            "session_id": self.session_id,
            "url": self.DG_URL,
        })

        try:
            self.ws = await asyncio.wait_for(
                websockets.connect(self.DG_URL, extra_headers=headers, max_size=None),
                timeout=self.connect_timeout_s,
            )
            self._opened = True
            self.emit("asr", "asr_open", {"session_id": self.session_id})
        except asyncio.TimeoutError as e:
            self.emit("asr", "asr_error", {
                "session_id": self.session_id,
                "error": f"connect_timeout:{self.connect_timeout_s}s",
            })
            raise
        except Exception as e:
            self.emit("asr", "asr_error", {
                "session_id": self.session_id,
                "error": f"connect_failed:{type(e).__name__}:{e}",
            })
            raise

        # Start receive loop
        self._recv_task = asyncio.create_task(self._recv_loop(), name=f"dg-recv-{self.session_id}")

    async def send_chunk(self, data: bytes, seq: Optional[int] = None):
        """
        Forward a user mic frame to Deepgram.

        Rules:
          • If this is the very first incoming frame and it's tiny (< min_valid_bytes),
            drop and log as 'drop_small_first_chunk'.
          • Otherwise send as binary and record last send time.
        """
        if not data:
            return

        if not self._first_real_chunk_sent and len(data) < self.min_valid_bytes:
            self.emit("asr", "drop_small_first_chunk", {
                "session_id": self.session_id,
                "bytes": len(data),
            })
            # Do not mark as first real chunk; just ignore the preamble.
            return

        self._first_real_chunk_sent = True

        if self.ws is None or not self._opened:
            # Not open; this is a logic error upstream.
            self.emit("asr", "asr_error", {
                "session_id": self.session_id,
                "error": "send_before_open",
            })
            return

        try:
            await self.ws.send(data)
            self._last_chunk_ts = time.time()
            self.emit("voice:chunk", "voice:chunk", {
                "session_id": self.session_id,
                "seq": seq if seq is not None else -1,
                "bytes": len(data),
            })
        except Exception as e:
            self.emit("asr", "asr_error", {
                "session_id": self.session_id,
                "error": f"send_failed:{type(e).__name__}:{e}",
            })
            raise

    async def end(self, wait_for_final: bool = True):
        """
        Graceful shutdown:
          1) Linger briefly after the last audio chunk.
          2) Send {"type":"CloseStream"} sentinel.
          3) Optionally wait for a final result (or timeout).
          4) Close WS.
        """
        if self._closing:
            return
        self._closing = True

        # 1) Linger a bit to let Deepgram flush interim → final
        now = time.time()
        if self._last_chunk_ts > 0:
            elapsed_ms = int((now - self._last_chunk_ts) * 1000)
            delay_ms = max(0, self.linger_ms - elapsed_ms)
            if delay_ms > 0:
                await asyncio.sleep(delay_ms / 1000.0)

        # 2) Explicit end-of-stream
        try:
            if self.ws is not None and self._opened:
                await self.ws.send(json.dumps({"type": "CloseStream"}))
        except Exception as e:
            # Not fatal — continue shutdown
            self.emit("asr", "asr_error", {
                "session_id": self.session_id,
                "error": f"close_stream_send_failed:{type(e).__name__}:{e}",
            })

        # 3) Wait for a final transcript (or the recv loop to mark final)
        if wait_for_final:
            try:
                await asyncio.wait_for(self._final_event.wait(), timeout=self.final_wait_s)
            except asyncio.TimeoutError:
                # No final arrived in time — log for diagnostics
                self.emit("asr", "asr_error", {
                    "session_id": self.session_id,
                    "error": f"final_timeout:{self.final_wait_s}s",
                })

        # 4) Close websocket
        try:
            if self.ws is not None:
                await self.ws.close()
                self.emit("ws_close", "ws_close", {"session_id": self.session_id})
        finally:
            self._opened = False

        # Ensure recv loop ends
        if self._recv_task:
            try:
                await asyncio.wait_for(self._recv_task, timeout=2.0)
            except asyncio.TimeoutError:
                self._recv_task.cancel()

    # -- receive loop ----------------------------------------------------------

    async def _recv_loop(self):
        """
        Consume Deepgram messages and surface partial/final results.
        """
        if self.ws is None:
            return

        deadline = time.time() + self.recv_max_wait_s
        try:
            while True:
                # Safety max wait guard: avoid dangling forever
                if time.time() > deadline and self._closing:
                    break

                try:
                    msg = await asyncio.wait_for(self.ws.recv(), timeout=2.5)
                except asyncio.TimeoutError:
                    # Normal idle; loop again
                    continue

                # Text frames from Deepgram carry JSON
                if isinstance(msg, (str, bytes)):
                    try:
                        payload = json.loads(msg if isinstance(msg, str) else msg.decode("utf-8"))
                    except Exception:
                        # Some control frames may not be JSON; ignore
                        continue

                    # Typical Deepgram schema: { "type": "Results", "channel": { "alternatives":[{"transcript":"...","confidence":...,"words":[...]}], "is_final": bool } }
                    evt_type = payload.get("type")
                    channel = payload.get("channel") or {}
                    is_final = bool(channel.get("is_final"))

                    # Emit partials/finals for admin trace
                    if evt_type == "Results" and channel.get("alternatives"):
                        alt = channel["alternatives"][0]
                        transcript = alt.get("transcript", "")
                        if transcript:
                            kind = "asr_result_final" if is_final else "asr_result_partial"
                            self.emit("asr", kind, {
                                "session_id": self.session_id,
                                "text": transcript,
                            })
                            self._any_result = True

                        if is_final:
                            # Signal final obtained
                            self._final_event.set()

                    # Some accounts emit VAD 'speech_final' events
                    if evt_type == "UtteranceEnd" or payload.get("speech_final") is True:
                        self._final_event.set()

                else:
                    # Non-text message types: ignore
                    continue

        except websockets.ConnectionClosed as e:
            # If we already got a final, this is normal. Otherwise, log.
            if not self._any_result and not self._closing:
                self.emit("asr", "asr_error", {
                    "session_id": self.session_id,
                    "error": f"recv_closed:{e.code}:{e.reason}",
                })
        except Exception as e:
            self.emit("asr", "asr_error", {
                "session_id": self.session_id,
                "error": f"recv_loop_error:{type(e).__name__}:{e}",
            })


# ---- Session registry (one ASR stream per diagnostics/user session) ----------

_streams: Dict[str, DeepgramStreamManager] = {}


def _get(api_key: str, session_id: str, emit: Optional[Callable[..., None]]):
    mgr = _streams.get(session_id)
    if mgr is None:
        mgr = DeepgramStreamManager(api_key=api_key, session_id=session_id, emit=emit)
        _streams[session_id] = mgr
    return mgr


# ---- Public API used by the diagnostics / app --------------------------------

async def asr_open(session_id: str, api_key: str, emit: Optional[Callable[..., None]] = None):
    """
    Open Deepgram realtime stream for this session.
    """
    mgr = _get(api_key=api_key, session_id=session_id, emit=emit)
    await mgr.open()


async def asr_send_chunk(session_id: str, data: bytes, seq: Optional[int] = None):
    """
    Forward one mic frame to Deepgram (binary Opus/PCM bytes).
    """
    mgr = _streams.get(session_id)
    if mgr is None:
        raise RuntimeError("asr_send_chunk called before asr_open")
    await mgr.send_chunk(data, seq=seq)


async def asr_end(session_id: str, wait_for_final: bool = True):
    """
    Finish the stream gracefully and close the socket after final (or timeout).
    """
    mgr = _streams.get(session_id)
    if mgr is None:
        return
    try:
        await mgr.end(wait_for_final=wait_for_final)
    finally:
        _streams.pop(session_id, None)
