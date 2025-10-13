
# app/obs.py — unified structured logging + Admin SSE mirror
from __future__ import annotations
import json, logging, os, time, traceback
from contextlib import contextmanager
from typing import Dict, Any

try:
    from app.admin_log import emit as _admin_emit
except Exception:
    def _admin_emit(*a, **k):
        pass

try:
    from .ws.bus import bus as _ws_bus
except Exception:
    _ws_bus = None

_log = logging.getLogger("askchip")

# Read once; env-driven
REDACT_EMAIL = (os.environ.get("REDACT_EMAIL_IN_LOGS","true").lower() in ("1","true","yes","on"))

def _ts() -> float:        # seconds (float)
    return time.time()

def _now_ms() -> int:
    return int(_ts() * 1000)

def _redact(value: str | None):
    if not value or not REDACT_EMAIL:
        return value
    try:
        v = str(value)
        # very light redact (avoid heavy CPU in hot path)
        v = v.replace("@", "ⓐ").replace(".", "·")
        return v
    except Exception:
        return value

def _broadcast_ws(kind: str, payload: Dict[str, Any]) -> None:
    if _ws_bus is None:
        return
    sid = payload.get("session_id") or payload.get("sid")
    if not sid:
        return
    try:
        sid_str = str(sid)
    except Exception:
        sid_str = sid  # type: ignore[assignment]

    frame = {"type": kind}
    frame.update(dict(payload))
    frame.setdefault("session_id", sid_str)
    frame.setdefault("sid", sid_str)

    try:
        _ws_bus.broadcast(str(sid_str), frame)
    except Exception:
        pass


def jlog(kind: str, **fields):
    """Emit one structured JSON log line and mirror to Admin SSE (best-effort)."""
    base = {
        "ts": _ts(),
        "kind": kind,
        "level": fields.pop("level", "info"),
    }
    # common correlation keys
    for k in ("session_id","turn_id","req_id","idem_key","component","phase"):
        if k in fields:
            base[k] = fields.pop(k)

    # redact long texts to head+len
    if "user_text" in fields:
        t = str(fields.pop("user_text") or "")
        base["user_text_head"] = _redact(t[:160])
        base["user_text_len"] = len(t)
    if "assistant_text" in fields:
        t = str(fields.pop("assistant_text") or "")
        base["assistant_text_head"] = t[:160]
        base["assistant_text_len"] = len(t)

    # merge remainder
    base.update(fields)

    _broadcast_ws(kind, base)

    try:
        _log.info(json.dumps(base, separators=(",", ":"), ensure_ascii=False))
    except Exception:
        _log.info(f'{{"ts":{_ts()},"kind":"{kind}","error":"json_dump_failed"}}')

    # SSE mirror
    try:
        _admin_emit(kind, **base)
    except Exception:
        pass

@contextmanager
def span(kind: str, **fields):
    t0 = _now_ms()
    err = None
    try:
        jlog(f"{kind}:start", **fields)
        yield
    except Exception as e:
        err = {"exc": e.__class__.__name__, "msg": str(e)}
        jlog(f"{kind}:error", **fields, error=err, stack=traceback.format_exc())
        raise
    finally:
        jlog(f"{kind}:done", **fields, dur_ms=_now_ms() - t0, error=err)
