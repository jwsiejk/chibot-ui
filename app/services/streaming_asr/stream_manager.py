# app/services/streaming_asr/stream_manager.py
from __future__ import annotations

import json
import os
import ssl
import threading
from queue import Queue, Empty
from typing import Dict, Optional, Any
from urllib.parse import urlencode

# Use the sync client from 'websockets' (already in your env).
from websockets.sync.client import connect as ws_connect
import websockets.exceptions as ws_exceptions

# Admin SSE emitter is optional; if missing, no-op.
try:
    from ...api_v1.admin import _emit  # type: ignore
except Exception:  # pragma: no cover
    def _emit(*_args, **_kwargs):
        return None


DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
DG_BASE = os.getenv("DEEPGRAM_WSS_URL", "wss://api.deepgram.com/v1/listen")

# Defaults for browser mic: WebM/Opus 48 kHz mono (your app path).
# If you ever test raw PCM to match the local script, you'd set encoding=linear16 & sample_rate=16000 here.
DG_PARAMS_DEFAULT = {
    "encoding": "opus",
    "sample_rate": 48000,
    "channels": 1,
    "interim_results": "true",
    "smart_format": "true",
    "punctuate": "true",
    "vad_events": "true",
    "utterance_end_ms": "1200",  # encourage timely finals
}

# Optional model/language overrides via env.
_opt_model = os.getenv("DG_MODEL", "").strip()
_opt_lang = os.getenv("DG_LANGUAGE", "").strip()
if _opt_model:
    DG_PARAMS_DEFAULT["model"] = _opt_model
if _opt_lang:
    DG_PARAMS_DEFAULT["language"] = _opt_lang


def _deepgram_listen_url() -> str:
    return f"{DG_BASE}?{urlencode(DG_PARAMS_DEFAULT)}"


def _bool_env(name: str) -> bool:
    v = os.getenv(name)
    return bool(v) and str(v).strip().lower() not in ("0", "false", "no")


def make_ssl_context() -> ssl.SSLContext:
    """
    Secure by default.
    - If DG_TLS_INSECURE=1 → disables verification (diagnostic only).
    - If DG_CAFILE is set → loads additional CA file (PEM) in addition to system bundle.
    """
    if _bool_env("DG_TLS_INSECURE"):
        # LAST RESORT — use only to confirm TLS interception is the blocker.
        return ssl._create_unverified_context()

    cafile = os.getenv("DG_CAFILE", "").strip() or None

    # Try system default first (Render should be fine)
    ctx = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)

    # If user provided an extra corporate root, add it
    if cafile:
        try:
            ctx.load_verify_locations(cafile=cafile)
        except Exception as e:
            _emit("asr", label="tls_warn", error=f"load_cafile_failed:{e}")

    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


class _DGSession:
    """
    One Deepgram streaming session per AskChip session_id (sid).
    open → send binary audio → receive JSON → count partials/finals.
    """

    def __init__(self, sid: str):
        self.sid = sid
        self.ws: Optional[Any] = None  # websockets.sync connection
        self.q: Queue[bytes] = Queue(maxsize=256)
        self.stop_flag = threading.Event()
        self.send_t: Optional[threading.Thread] = None
        self.recv_t: Optional[threading.Thread] = None

        # counters used by Diagnostics
        self.partials = 0
        self.finals = 0
        self.error: Optional[str] = None

    # ---- lifecycle ---------------------------------------------------------

    def start(self):
        if not DEEPGRAM_API_KEY:
            self.error = "missing_deepgram_key"
            _emit("asr", label="asr_error", session_id=self.sid, error=self.error)
            return

        headers = [("Authorization", f"Token {DEEPGRAM_API_KEY}")]
        ctx = make_ssl_context()

        url = _deepgram_listen_url()
        try:
            # websockets.sync uses 'additional_headers' and 'ssl'
            self.ws = ws_connect(
                url,
                additional_headers=headers,
                ssl=ctx,
                open_timeout=15,
            )
            _emit(
                "asr",
                label="provider_open",
                provider="deepgram",
                session_id=self.sid,
                url=url,
            )
        except Exception as e:  # connection error
            self.error = f"provider_connect:{type(e).__name__}:{e}"
            _emit("asr", label="asr_error", session_id=self.sid, error=self.error)
            return

        # start IO threads
        self.send_t = threading.Thread(target=self._send_loop, name=f"dg-send-{self.sid}", daemon=True)
        self.recv_t = threading.Thread(target=self._recv_loop, name=f"dg-recv-{self.sid}", daemon=True)
        self.send_t.start()
        self.recv_t.start()

    def close(self):
        self.stop_flag.set()
        try:
            if self.ws is not None:
                self.ws.close()
        except Exception:
            pass
        _emit("asr", label="provider_close", provider="deepgram", session_id=self.sid)

    # ---- public ------------------------------------------------------------

    def enqueue(self, audio_bytes: bytes):
        if self.stop_flag.is_set():
            return
        try:
            self.q.put_nowait(audio_bytes)
        except Exception:
            # drop oldest if full to avoid unbounded growth
            try:
                _ = self.q.get_nowait()
            except Exception:
                pass
            try:
                self.q.put_nowait(audio_bytes)
            except Exception:
                pass

    # ---- internals ---------------------------------------------------------

    def _send_loop(self):
        """Send binary audio frames to Deepgram (bytes → binary opcode automatically)."""
        try:
            while not self.stop_flag.is_set():
                try:
                    buf = self.q.get(timeout=0.25)
                except Empty:
                    continue
                if not buf or self.ws is None:
                    continue
                try:
                    self.ws.send(buf)  # bytes → binary frame
                except Exception as e:
                    self.error = f"send_error:{type(e).__name__}:{e}"
                    _emit("asr", label="asr_error", session_id=self.sid, error=self.error)
                    self.stop_flag.set()
                    break
        finally:
            try:
                if self.ws is not None:
                    self.ws.close()
            except Exception:
                pass

    def _recv_loop(self):
        """Receive JSON messages; count partials/finals."""
        try:
            while not self.stop_flag.is_set() and self.ws is not None:
                try:
                    msg = self.ws.recv()
                except ws_exceptions.ConnectionClosed as e:
                    code = getattr(e, "code", None)
                    reason = getattr(e, "reason", "")
                    # Normal shutdown (1000/1001) → just note and exit
                    if code in (1000, 1001):
                        _emit("asr", label="provider_closed", session_id=self.sid, code=code, reason=reason)
                    else:
                        self.error = f"recv_closed:{code}:{reason}"
                        _emit("asr", label="asr_error", session_id=self.sid, error=self.error)
                    self.stop_flag.set()
                    break
                except Exception as e:
                    self.error = f"recv_error:{type(e).__name__}:{e}"
                    _emit("asr", label="asr_error", session_id=self.sid, error=self.error)
                    self.stop_flag.set()
                    break

                if isinstance(msg, (bytes, bytearray)):
                    continue  # ignore any binary echoes

                try:
                    data = json.loads(msg)
                except Exception:
                    continue

                # Deepgram result frames include 'is_final' on transcripts.
                is_final = data.get("is_final")
                if is_final is True:
                    self.finals += 1
                    _emit("asr", label="final", session_id=self.sid, finals=self.finals)
                elif is_final is False:
                    self.partials += 1
                    _emit("asr", label="partial", session_id=self.sid, partials=self.partials)
        finally:
            self.stop_flag.set()
            try:
                if self.ws is not None:
                    self.ws.close()
            except Exception:
                pass


# --------------------- Manager (singleton) -----------------------------------

class StreamManager:
    """Creates/owns _DGSession instances keyed by AskChip session id."""

    def __init__(self):
        self._lock = threading.Lock()
        self._sessions: Dict[str, _DGSession] = {}

    def _ensure(self, sid: str) -> _DGSession:
        with self._lock:
            s = self._sessions.get(sid)
            if s is None or s.stop_flag.is_set():
                s = _DGSession(sid)
                self._sessions[sid] = s
                s.start()
            return s

    def enqueue(self, sid: str, item: dict):
        """item = {'data': bytes, 'user_msg_id': str|None, 'chunk_seq': int}"""
        data = item.get("data")
        if not data:
            return
        sess = self._ensure(sid)
        sess.enqueue(data)

    def stats(self, sid: str) -> dict:
        with self._lock:
            s = self._sessions.get(sid)
            if not s:
                return {"partials": 0, "finals": 0, "err": None}
            return {"partials": s.partials, "finals": s.finals, "err": s.error}

    def stats_all(self) -> dict:
        """Aggregate counters across all active sessions (for Diagnostics GET without sid)."""
        with self._lock:
            partials = finals = err_count = 0
            sessions: Dict[str, dict] = {}
            for sid, s in self._sessions.items():
                sessions[sid] = {"partials": s.partials, "finals": s.finals, "err": s.error}
                partials += s.partials
                finals += s.finals
                if s.error:
                    err_count += 1
            return {"partials": partials, "finals": finals, "err_count": err_count, "sessions": sessions}

    def close(self, sid: str):
        with self._lock:
            s = self._sessions.pop(sid, None)
            if s:
                s.close()


# global singleton
_MANAGER: Optional[StreamManager] = None


def get_manager() -> StreamManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = StreamManager()
    return _MANAGER
