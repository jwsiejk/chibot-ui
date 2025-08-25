from __future__ import annotations
import os, time, threading, re
from collections import deque
from typing import Any, Dict, List, Tuple

# In‑memory ring buffer (process local). Good enough for live debugging in dev/prod single instance.
_MAX = int(os.getenv("CALL_LOG_MAX", "400"))
_BUF: deque[Dict[str, Any]] = deque(maxlen=_MAX)
_LOCK = threading.Lock()

def _admin_email_set() -> set[str]:
    raw = os.getenv("ADMIN_EMAILS", "") or os.getenv("ADMIN_EMAIL", "")
    parts = [p.strip().lower() for p in re.split(r"[\s,;]+", raw) if p.strip()]
    return set(parts)

def is_admin(email: str | None) -> bool:
    return (email or "").lower().strip() in _admin_email_set()

# Light redaction for payloads (avoid leaking keys/secrets in UI)
_REDACT_PATTERNS = [
    (re.compile(r"(sk-[A-Za-z0-9]{8,})"), "sk-***"),
    (re.compile(r"(?i)(authorization|api[_-]?key|xi-api-key)\s*[:=]\s*([A-Za-z0-9._-]{8,})"), "\1=***"),
    (re.compile(r"(?i)(OPENAI_API_KEY|ELEVEN.*API.*|SMTP_PASS)\s*[:=]\s*([^\s,]+)"), "\1=***"),
]

def _redact(val: Any) -> Any:
    try:
        s = str(val)
        for pat, repl in _REDACT_PATTERNS:
            s = pat.sub(repl, s)
        return s
    except Exception:
        return val

def log_event(kind: str, *, email: str | None = None, route: str | None = None, **fields: Any) -> None:
    ev = {"ts": time.time(), "kind": kind, "email": (email or "").lower(), "route": route or ""}
    if fields:
        ev["fields"] = {k: _redact(v) for k, v in fields.items()}
    with _LOCK:
        _BUF.append(ev)

def recent(limit: int = 200) -> List[Dict[str, Any]]:
    with _LOCK:
        return list(_BUF)[-int(max(1, min(limit, _MAX))):]

def clear() -> int:
    with _LOCK:
        n = len(_BUF)
        _BUF.clear()
        return n
