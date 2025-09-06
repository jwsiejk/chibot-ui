from __future__ import annotations
import os, json, time, uuid, datetime as dt
from typing import Optional, Tuple
from app.db_dal import DAL, DBConfig

SMTP_FAILS = int(os.environ.get("SMTP_FAILS_FOR_TEST", "0"))  # for tests

def _dal():
    url = os.environ.get("DATABASE_URL", "sqlite:///ci_phase15.sqlite3")
    return DAL(DBConfig(url=url))

def enqueue_item(kind: str, dedupe_key: Optional[Tuple[str,str]], payload: dict, session_id: Optional[str]=None, ended_at: Optional[str]=None) -> str:
    oid = str(uuid.uuid4())
    dal = _dal()
    # Ensure table exists (CI)
    try:
        dal.execute("CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY, kind TEXT, session_id TEXT, ended_at TEXT, payload_json TEXT, status TEXT, attempts INTEGER, next_attempt_at TEXT, last_error TEXT, created_at TEXT, updated_at TEXT)")
    except Exception:
        pass
    if dedupe_key and all(dedupe_key):
        sid, ended = dedupe_key
        # Unique constraint may not exist on sqlite in CI; enforce manually
        rows = dal.query("SELECT id FROM outbox WHERE session_id=? AND ended_at=? AND kind=?", (sid, ended, kind))
        if rows:
            return rows[0][0] if isinstance(rows[0], tuple) else rows[0]["id"]
    dal.execute("INSERT INTO outbox (id, kind, session_id, ended_at, payload_json, status, attempts) VALUES (?,?,?,?,?,'queued',0)", (oid, kind, session_id, ended_at, json.dumps(payload)))
    return oid

def _send_smtp(payload: dict):
    # Placeholder SMTP sender; injected failures for tests
    global SMTP_FAILS
    if SMTP_FAILS > 0:
        SMTP_FAILS -= 1
        raise RuntimeError("Simulated SMTP transient failure")
    # pretend success
    return True

def _now():
    return dt.datetime.utcnow()

def process_once(limit: int=10):
    dal = _dal()
    rows = dal.query("SELECT id, kind, payload_json, attempts FROM outbox WHERE status='queued' AND (next_attempt_at IS NULL OR next_attempt_at <= ?) ORDER BY created_at LIMIT ?", (str(_now()), limit))
    processed = 0
    for r in rows:
        oid = r[0]
        kind = r[1]
        payload = json.loads(r[2])
        attempts = r[3] or 0
        try:
            _send_smtp(payload) if kind=="transcript_email" else None
            dal.execute("UPDATE outbox SET status='sent', updated_at=? WHERE id=?", (str(_now()), oid))
            processed += 1
        except Exception as e:
            attempts += 1
            backoff = min(60, 2 ** attempts)  # simple backoff
            na = _now() + dt.timedelta(seconds=backoff)
            dal.execute("UPDATE outbox SET attempts=?, last_error=?, next_attempt_at=?, updated_at=? WHERE id=?", (attempts, str(e), str(na), str(_now()), oid))
    return processed
