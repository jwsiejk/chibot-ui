from __future__ import annotations
import time, threading, os, uuid
from typing import Dict, Any, Optional, Callable

# Minimal pluggable metrics adapter
class MetricsSink:
    def incr(self, name: str, value: int=1, tags: Optional[Dict[str,str]]=None): pass
    def timing(self, name: str, ms: float, tags: Optional[Dict[str,str]]=None): pass
    def gauge(self, name: str, value: float, tags: Optional[Dict[str,str]]=None): pass

class MemorySink(MetricsSink):
    def __init__(self):
        self.events = []
    def incr(self, name, value=1, tags=None):
        self.events.append(("incr", name, value, tags or {}))
    def timing(self, name, ms, tags=None):
        self.events.append(("timing", name, ms, tags or {}))
    def gauge(self, name, value, tags=None):
        self.events.append(("gauge", name, value, tags or {}))

_sink: MetricsSink = MemorySink()
_sink_lock = threading.RLock()

def set_sink(s: MetricsSink):
    global _sink
    with _sink_lock:
        _sink = s

def get_sink() -> MetricsSink:
    return _sink

def with_correlation_id(headers: Optional[Dict[str,str]]=None) -> str:
    """Return correlation id; prefer incoming header else new uuid."""
    if headers and "x-correlation-id" in {k.lower():v for k,v in headers.items()}:
        # normalize: headers may have case variants
        for k,v in headers.items():
            if k.lower()=="x-correlation-id":
                return v
    return str(uuid.uuid4())

def emit_request_metrics(kind: str, ok: bool, latency_ms: float, extra_tags: Optional[Dict[str,str]]=None):
    tags = {"kind": kind, "ok": "true" if ok else "false"}
    if extra_tags: tags.update(extra_tags)
    s = get_sink()
    s.incr("askchip.requests", 1, tags)
    s.timing("askchip.latency_ms", latency_ms, tags)

# Convenience wrappers per subsystem
def llm_timing(ms: float, ok: bool, model: str=""):
    get_sink().timing("askchip.llm.latency_ms", ms, {"ok": str(ok).lower(), "model": model})
def stt_timing(ms: float, ok: bool, lang: str="en"):
    get_sink().timing("askchip.stt.latency_ms", ms, {"ok": str(ok).lower(), "lang": lang})
def tts_timing(ms: float, ok: bool, voice: str=""):
    get_sink().timing("askchip.tts.latency_ms", ms, {"ok": str(ok).lower(), "voice": voice})
def smtp_timing(ms: float, ok: bool):
    get_sink().timing("askchip.smtp.latency_ms", ms, {"ok": str(ok).lower()})
def ws_disconnect(reason: str):
    get_sink().incr("askchip.ws.disconnects", 1, {"reason": reason})
def cost_tally(kind: str, cents: float):
    get_sink().gauge("askchip.cost.cents", cents, {"kind": kind})
