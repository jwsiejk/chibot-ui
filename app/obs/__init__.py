
from __future__ import annotations
import json, logging, os, time, traceback
from contextlib import contextmanager

_log = logging.getLogger("askchip")
REDACT_EMAIL = (os.environ.get("REDACT_EMAIL_IN_LOGS","true").lower() in ("1","true","yes","on"))

def _ts() -> float: return time.time()
def _now_ms() -> int: return int(_ts() * 1000)

def _redact(value):
    if not value or not REDACT_EMAIL: return value
    try:
        v = str(value).replace("@","ⓐ").replace(".","·")
        return v
    except Exception:
        return value

def jlog(kind: str, **fields):
    base = {"ts": _ts(), "kind": kind, "level": fields.pop("level", "info")}
    for k in ("session_id","turn_id","req_id","idem_key","component","phase"):
        if k in fields: base[k] = fields.pop(k)
    if "user_text" in fields:
        t = str(fields.pop("user_text") or "")
        base["user_text_head"] = _redact(t[:160]); base["user_text_len"] = len(t)
    if "assistant_text" in fields:
        t = str(fields.pop("assistant_text") or "")
        base["assistant_text_head"] = t[:160]; base["assistant_text_len"] = len(t)
    base.update(fields)
    try:
        _log.info(json.dumps(base, separators=(",", ":"), ensure_ascii=False))
    except Exception:
        _log.info(f'{{"ts":{_ts()},"kind":"{kind}","error":"json_dump_failed"}}')
    try:
        from app.api_v1.admin import _emit
        _emit(kind, **base)
    except Exception:
        pass

@contextmanager
def span(kind: str, **fields):
    t0 = _now_ms(); err = None
    try:
        jlog(f"{kind}:start", **fields)
        yield
    except Exception as e:
        err = {"exc": e.__class__.__name__, "msg": str(e)}
        jlog(f"{kind}:error", **fields, error=err, stack=traceback.format_exc())
        raise
    finally:
        jlog(f"{kind}:done", **fields, dur_ms=_now_ms() - t0, error=err)
