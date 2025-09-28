from __future__ import annotations
import uuid, datetime as _dt
from typing import Tuple

from ..db import db  # your Neon connection/adapter

DEFAULT_TTL_SEC = 600

def _ttl_expire_at(ttl_sec: int) -> str:
    return f"now() + interval '{int(ttl_sec)} seconds'"

def get_or_create_greet_turn(
    session_id: str,
    *,
    force: bool = False,
    ttl_sec: int = DEFAULT_TTL_SEC,
) -> Tuple[str, bool]:
    """
    Returns (turn_id, idempotent).
    - If an unexpired greet exists and not force: returns existing turn_id, idempotent=True.
    - Else creates/refreshes a new turn_id with fresh TTL, idempotent=False.
    Uses two simple, race-safe steps with UPSERT—OK at current QPS.
    """
    sid = (session_id or "default").strip() or "default"

    # Step 1: Try to reuse a recent greet unless force=True
    if not force:
        row = db.sql_one("""
            SELECT turn_id
            FROM greet_turns
            WHERE session_id = %(sid)s AND expires_at > now()
            LIMIT 1
        """, {"sid": sid})
        if row and row.get("turn_id"):
            return str(row["turn_id"]), True

    # Step 2: Create/refresh a greet turn (UPSERT)
    new_tid = uuid.uuid4().hex
    db.sql("""
        INSERT INTO greet_turns (session_id, turn_id, created_at, expires_at)
        VALUES (%(sid)s, %(tid)s, now(), """ + _ttl_expire_at(ttl_sec) + """)
        ON CONFLICT (session_id) DO UPDATE
          SET turn_id = EXCLUDED.turn_id,
              created_at = now(),
              expires_at = """ + _ttl_expire_at(ttl_sec) + """
    """, {"sid": sid, "tid": new_tid})

    return new_tid, False
