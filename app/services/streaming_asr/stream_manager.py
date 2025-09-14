
from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from typing import Deque, Dict, Optional, Any

from app.ws.bus import bus
from app.config_store import get_config
from .deepgram_client import DeepgramClient

# Simple circuit breaker counters (kept in-process)
_METRICS: Dict[str, int] = {
    "partials": 0,
    "finals": 0,
    "queue_drops": 0,
    "provider_errors": 0,
    "sessions": 0,
}

_MAX_QUEUE = 128
_IDLE_CLOSE_MS = 7_000           # close if no chunks for this long
_CB_OPEN_UNTIL_MS = 0            # epoch ms until we allow new sessions
_CB_BACKOFF_MS = 10_000          # after provider error, back off

def _now_ms() -> int:
    return int(time.time() * 1000)

def _cb_opened() -> bool:
    return _now_ms() < _CB_OPEN_UNTIL_MS

class _Session:
    def __init__(self, sid: str, cfg: Dict[str, Any]):
        self.sid = sid
        self.cfg = cfg
        self.q: Deque[Dict[str, Any]] = deque()
        self.last_msg_ms = _now_ms()
        self.user_msg_id: Optional[str] = None
        self._closing = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        t = threading.Thread(target=self._run, daemon=True, name=f"asr-{self.sid}")
        self._thread = t
        t.start()

    def enqueue(self, item: Dict[str, Any]):
        # Drop oldest if full
        if len(self.q) >= _MAX_QUEUE:
            self.q.popleft()
            _METRICS["queue_drops"] += 1
        self.q.append(item)
        self.last_msg_ms = _now_ms()

    def shutdown(self):
        self._closing = True

    def _run(self):
        global _CB_OPEN_UNTIL_MS
        try:
            cfg = get_config() or {}
        except Exception:
            cfg = {}
        # fail fast if breaker open
        if _cb_opened():
            bus.broadcast(self.sid, {"type":"asr_error","error":"provider_backoff"})
            return

        try:
            client = DeepgramClient(cfg)
        except Exception as e:
            _METRICS["provider_errors"] += 1
            _CB_OPEN_UNTIL_MS = _now_ms() + _CB_BACKOFF_MS
            bus.broadcast(self.sid, {"type":"asr_error","error":str(e)})
            return

        async def stream_loop():
            await client.connect()
            poll_task = asyncio.create_task(_poll_events(client, self.sid, self))
            try:
                while not self._closing:
                    # idle close
                    if self.q:
                        item = self.q.popleft()
                    else:
                        if _now_ms() - self.last_msg_ms > _IDLE_CLOSE_MS:
                            break
                        await asyncio.sleep(0.01)
                        continue

                    data = item.get("data") or b""
                    if self.user_msg_id is None:
                        self.user_msg_id = item.get("user_msg_id")
                    try:
                        await client.send(data)
                    except Exception as e:
                        _METRICS["provider_errors"] += 1
                        bus.broadcast(self.sid, {"type":"asr_error","error":"send_failed"})
                        break
            finally:
                try:
                    await client.close()
                except Exception:
                    pass
                try:
                    poll_task.cancel()
                except Exception:
                    pass

        asyncio.run(stream_loop())

def _asr_partial_frame(sid: str, sess: _Session, text: str) -> Dict[str, Any]:
    _METRICS["partials"] += 1
    fr = {"type":"user_partial","text":text}
    if sess.user_msg_id:
        fr["user_msg_id"] = sess.user_msg_id
    return fr

def _asr_final_frame(sid: str, sess: _Session, text: str) -> Dict[str, Any]:
    _METRICS["finals"] += 1
    fr = {"type":"user_final","text":text}
    if sess.user_msg_id:
        fr["user_msg_id"] = sess.user_msg_id
    return fr

async def _poll_events(client: DeepgramClient, sid: str, sess: _Session):
    try:
        async for evt in client.poll_events(timeout=0.05):
            if not isinstance(evt, dict):
                continue
            t = evt.get("type")
            if t == "user_partial":
                bus.broadcast(sid, _asr_partial_frame(sid, sess, evt.get("text","")))
            elif t == "user_final":
                bus.broadcast(sid, _asr_final_frame(sid, sess, evt.get("text","")))
            elif t == "noop":
                await asyncio.sleep(0.005)
            else:
                # ignore unknown
                await asyncio.sleep(0.001)
    except Exception as e:
        _METRICS["provider_errors"] += 1
        bus.broadcast(sid, {"type":"asr_error","error":"poll_failed"})

# ---- global manager facade ----

_MANAGER: Optional["_Manager"] = None

class _Manager:
    def __init__(self):
        self._sessions: Dict[str, _Session] = {}

    def get(self, sid: str) -> _Session:
        sess = self._sessions.get(sid)
        if not sess:
            sess = _Session(sid, get_config())
            self._sessions[sid] = sess
            _METRICS["sessions"] += 1
            sess.start()
        return sess

    def enqueue(self, sid: str, item: Dict[str, Any]):
        self.get(sid).enqueue(item)

    def shutdown(self):
        for sess in list(self._sessions.values()):
            sess.shutdown()

def get_manager() -> _Manager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = _Manager()
    return _MANAGER

async def shutdown_manager():
    mgr = _MANAGER
    if mgr is not None:
        mgr.shutdown()

# ---- status for diagnostics ----
def get_streaming_status() -> Dict[str, object]:
    return {
        "breaker_open": _cb_opened(),
        "provider_errors": _METRICS["provider_errors"],
        "partials": _METRICS["partials"],
        "finals": _METRICS["finals"],
        "sessions": _METRICS["sessions"],
    }
