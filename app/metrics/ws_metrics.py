from __future__ import annotations
import time
from typing import Dict, List


def summarize_active_ws() -> dict:
    """Best-effort snapshot of active websocket connections."""
    try:
        from app.ws import ws_asgi
    except Exception as exc:
        return {"ok": False, "error": f"import_{exc.__class__.__name__}"}

    registry = getattr(ws_asgi, "ACTIVE_WS", None)
    if registry is None:
        return {"ok": False, "error": "registry_unavailable"}

    try:
        entries = list(registry.items())
    except RuntimeError:
        # If the registry is being mutated concurrently, retry once.
        entries = list(registry.items())

    return {
        "ok": True,
        "active": len(registry),
        "connections": [
            {"conn_id": conn_id, **meta} for conn_id, meta in entries
        ],
        "generated_ts": time.time(),
    }

FAILED_HANDSHAKES: Dict[str, List[float]] = {}
TOTAL_FAILS: int = 0
OVERLIMIT_FAILS: int = 0
PROCESS_START_TS = time.time()

def _purge(q: List[float], now: float, window_sec: float) -> None:
    cutoff = now - window_sec
    i = 0
    for t in q:
        if t >= cutoff:
            break
        i += 1
    if i:
        del q[:i]

def record_fail(ip: str, limit: int, window_sec: float) -> bool:
    global TOTAL_FAILS, OVERLIMIT_FAILS
    TOTAL_FAILS += 1
    q = FAILED_HANDSHAKES.setdefault(ip, [])
    now = time.time()
    _purge(q, now, window_sec)
    q.append(now)
    over = len(q) > limit
    if over:
        OVERLIMIT_FAILS += 1
    return over

def snapshot() -> dict:
    return {
        "total_fails": TOTAL_FAILS,
        "overlimit_fails": OVERLIMIT_FAILS,
        "ips": {ip: len(ts) for ip, ts in FAILED_HANDSHAKES.items()},
        "process_start_ts": PROCESS_START_TS,
        "now": time.time()
    }
