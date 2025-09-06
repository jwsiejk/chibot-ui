from __future__ import annotations
import os, time, sqlite3, threading, contextlib, json, random, uuid
from dataclasses import dataclass
from typing import Optional, Any, Callable, Iterable, Tuple

# Simple DAL supporting sqlite (tests/CI) and Postgres via psycopg2 if available.
try:
    import psycopg
    HAVE_PG = True
except Exception:
    HAVE_PG = False

DEFAULT_MAX_RETRIES = int(os.environ.get("DB_MAX_RETRIES", "3"))
DEFAULT_BASE_DELAY = float(os.environ.get("DB_BASE_DELAY", "0.08"))  # seconds
DEFAULT_MAX_DELAY = float(os.environ.get("DB_MAX_DELAY", "1.0"))

@dataclass
class DBConfig:
    url: str
    max_retries: int = DEFAULT_MAX_RETRIES
    base_delay: float = DEFAULT_BASE_DELAY
    max_delay: float = DEFAULT_MAX_DELAY

class DAL:
    def __init__(self, cfg: DBConfig):
        cfg.url = (cfg.url or '').strip()
        self.cfg = cfg
        self._lock = threading.RLock()
        self._pg_pool = None
        if self.cfg.url.startswith("postgres") and HAVE_PG:
            # lightweight pool using psycopg.ConnectionPool if available
            try:
                from psycopg_pool import ConnectionPool  # optional module
            except Exception:
                ConnectionPool = None
            if ConnectionPool:
                self._pg_pool = ConnectionPool(self.cfg.url, min_size=1, max_size=4, kwargs={"connect_timeout": 3})
        # else: sqlite fallback

    @contextlib.contextmanager
    def connect(self):
        if self.cfg.url.startswith("postgres") and HAVE_PG and self._pg_pool:
            with self._pg_pool.connection() as conn:
                yield conn
        elif self.cfg.url.startswith("postgres") and HAVE_PG:
            conn = psycopg.connect(self.cfg.url, connect_timeout=3)
            try:
                yield conn
            finally:
                conn.close()
        else:
            # sqlite
            path = self.cfg.url.replace("sqlite:///","")
            conn = sqlite3.connect(path)
            try:
                yield conn
            finally:
                conn.close()

    def _retry(self, fn: Callable[[], Any]):
        attempts = 0
        delay = self.cfg.base_delay
        while True:
            try:
                return fn()
            except Exception as e:
                attempts += 1
                if attempts > self.cfg.max_retries:
                    raise
                time.sleep(min(delay, self.cfg.max_delay))
                delay *= 2.0

    def execute(self, sql: str, params: Optional[Iterable[Any]] = None):
        def _do():
            with self.connect() as c:
                cur = c.cursor()
                if params is None:
                    cur.execute(sql)
                else:
                    cur.execute(sql, params)
                c.commit()
                return cur.rowcount
        return self._retry(_do)

    def query(self, sql: str, params: Optional[Iterable[Any]] = None) -> list[Tuple]:
        def _do():
            with self.connect() as c:
                c.row_factory = sqlite3.Row if hasattr(c, "row_factory") else None
                cur = c.cursor()
                if params is None:
                    cur.execute(sql)
                else:
                    cur.execute(sql, params)
                rows = cur.fetchall()
                return rows
        return self._retry(_do)

# Health check & retention helpers
def health_check(dal: DAL) -> dict:
    try:
        dal.query("SELECT 1")
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def anonymize_user(dal: DAL, email: str) -> int:
    # Overwrite PII-like fields and summaries
    # Using generic column names for safety; ignore errors if columns don't exist.
    total = 0
    try:
        total += dal.execute("UPDATE users SET name='(anonymized)', title=NULL, region=NULL WHERE email=?", (email,))
    except Exception:
        pass
    try:
        total += dal.execute("UPDATE memory_facts SET value_jsonb='{}' WHERE email=?", (email,))
    except Exception:
        pass
    return total

def delete_user_data(dal: DAL, email: str) -> int:
    total = 0
    # Delete messages and sessions for user
    try:
        # Gather session ids
        sess_rows = dal.query("SELECT id FROM sessions WHERE email=?", (email,))
        sess_ids = [r[0] if isinstance(r, tuple) else r["id"] for r in sess_rows]
        for sid in sess_ids:
            try:
                dal.execute("DELETE FROM messages WHERE session_id=?", (sid,))
            except Exception:
                pass
        total += dal.execute("DELETE FROM sessions WHERE email=?", (email,))
    except Exception:
        pass
    try:
        total += dal.execute("DELETE FROM logs WHERE email=?", (email,))
    except Exception:
        pass
    return total
