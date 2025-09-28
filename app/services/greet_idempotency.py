from __future__ import annotations
import uuid, time
from typing import Tuple
from ..db import db

DEFAULT_TTL_SEC = 600

def _now() -> float:
    try:
        return time.time()
    except Exception:
        return 0.0

def _get_mem_table():
    mem = getattr(db, "memory", None)
    if isinstance(mem, dict):
        return mem.setdefault("greet_turns_mem", {})
    if not hasattr(_get_mem_table, "_tbl"):
        _get_mem_table._tbl = {}
    return _get_mem_table._tbl  # type: ignore[attr-defined]

def clear_greet_turn_cache(session_id: str) -> None:
    """Clear cached greet turn metadata for the given session identifier."""
    sid = (session_id or "default").strip() or "default"
    try:
        mem = getattr(db, "memory", None)
        if isinstance(mem, dict):
            mem.setdefault("greet_turns", {}).pop(sid, None)
    except Exception:
        pass
    try:
        tbl = _get_mem_table()
        if isinstance(tbl, dict):
            tbl.pop(sid, None)
    except Exception:
        pass
        
def get_or_create_greet_turn(session_id: str, *, force: bool=False, ttl_sec: int=DEFAULT_TTL_SEC) -> Tuple[str, bool]:
    """Return the greet turn identifier for the provided session.

    Returns (turn_id, idempotent). Works in two modes:
    1) Neon/DAL mode (preferred): uses db.sql_one / db.sql against table greet_turns.
    2) Dev/in-memory mode: falls back to db.memory['greet_turns_mem'] with TTL.
    """
    sid = (session_id or "default").strip() or "default"

    # --- Mode 1: Neon / DAL available ---
    if hasattr(db, "sql_one") and hasattr(db, "sql"):
        if not force:
            row = db.sql_one("""
                SELECT turn_id
                FROM greet_turns
                WHERE session_id = %(sid)s AND expires_at > now()
                LIMIT 1
            """, {"sid": sid})
            if row and row.get("turn_id"):
                return str(row["turn_id"]), True

        new_tid = uuid.uuid4().hex
        db.sql(
            """
            INSERT INTO greet_turns (session_id, turn_id, created_at, expires_at)
            VALUES (%(sid)s, %(tid)s, now(), now() + interval '%(ttl)s seconds')
            ON CONFLICT (session_id) DO UPDATE
              SET turn_id = EXCLUDED.turn_id,
                  created_at = now(),
                  expires_at = now() + interval '%(ttl)s seconds'
            """.replace("%(ttl)s", str(int(ttl_sec))),
            {"sid": sid, "tid": new_tid}
        )
        return new_tid, False

    # --- Mode 2: In-memory fallback (dev/testing) ---
    tbl = _get_mem_table()
    now = _now()
    # purge expired
    try:
        expired = [k for k, v in tbl.items() if isinstance(v, dict) and float(v.get("exp", 0.0)) <= now]
        for k in expired:
            tbl.pop(k, None)
    except Exception:
        pass

    if not force and sid in tbl:
        v = tbl.get(sid) or {}
        tid = str(v.get("tid")) if isinstance(v, dict) else str(v)
        if tid:
            return tid, True

    new_tid = uuid.uuid4().hex
    tbl[sid] = {"tid": new_tid, "exp": now + max(1, int(ttl_sec))}
    return new_tid, False
