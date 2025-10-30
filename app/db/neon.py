"""Neon database pool and helpers."""
from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from typing import Any, Dict, Optional

import asyncpg

from app.db.postgres_config import get_postgres_config, redact_dsn

_log = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None
_pool_lock = asyncio.Lock()
_schema_lock = asyncio.Lock()
_schema_initialized = False

_status: Dict[str, Any] = {"state": "init", "last_error": None, "updated_at": time.time()}

_DB_CONNECT_TIMEOUT_S = float(os.getenv("DB_CONNECT_TIMEOUT_S", "45"))
_DB_RETRY_ATTEMPTS = int(os.getenv("DB_RETRY_ATTEMPTS", "10"))
_DB_RETRY_BASE_S = float(os.getenv("DB_RETRY_BASE_S", "1.0"))
_DB_RETRY_CAP_S = float(os.getenv("DB_RETRY_CAP_S", "30.0"))


def _update_status(**fields: Any) -> None:
    _status.update(fields)
    _status["updated_at"] = time.time()


async def get_pool() -> asyncpg.Pool:
    """Return a lazily created asyncpg pool."""

    global _pool
    if _pool is None:
        async with _pool_lock:
            if _pool is None:
                config = get_postgres_config()
                dsn = config.main.dsn
                if not dsn:
                    _update_status(state="error", last_error="MissingDatabaseURL")
                    _log.error("evt=db_connect_missing_url")
                    raise ValueError("DATABASE_URL is required")

                attempt = 0
                for attempt in range(1, _DB_RETRY_ATTEMPTS + 1):
                    state = "connecting" if attempt == 1 else "retrying"
                    _update_status(state=state, last_error=None, attempt=attempt, timeout_s=_DB_CONNECT_TIMEOUT_S)
                    _log.info(
                        "evt=db_connect_attempt attempt=%s timeout_s=%s",
                        attempt,
                        _DB_CONNECT_TIMEOUT_S,
                    )
                    try:
                        _pool = await asyncpg.create_pool(
                            dsn,
                            statement_cache_size=0,
                            min_size=0,
                            max_size=10,
                            timeout=_DB_CONNECT_TIMEOUT_S,
                            max_inactive_connection_lifetime=180,
                        )
                        break
                    except Exception as exc:  # pragma: no cover - network failures
                        if attempt < _DB_RETRY_ATTEMPTS:
                            base_wait = min(_DB_RETRY_CAP_S, _DB_RETRY_BASE_S * (2 ** (attempt - 1)))
                            jitter = random.uniform(0, base_wait)
                            wait_seconds = min(_DB_RETRY_CAP_S, base_wait + jitter)
                        else:
                            wait_seconds = 0.0
                        _update_status(state="retrying", last_error=exc.__class__.__name__, attempt=attempt)
                        _log.info(
                            "evt=db_connect_retry attempt=%s wait_s=%.2f err=%s",
                            attempt,
                            wait_seconds,
                            exc.__class__.__name__,
                        )
                        if attempt >= _DB_RETRY_ATTEMPTS:
                            _update_status(state="error", last_error=exc.__class__.__name__, attempt=attempt)
                            _log.error("evt=db_connect_failed err=%s", exc.__class__.__name__)
                            raise
                        await asyncio.sleep(wait_seconds)
                else:  # pragma: no cover
                    raise RuntimeError("DB connection attempts exhausted")

                assert _pool is not None
                _update_status(state="connected", last_error=None, attempt=attempt)
                _log.info("evt=db_connect_ok attempt=%s", attempt)
                _log.info("evt=db_pool_ready dsn=%s", redact_dsn(dsn))
    assert _pool is not None
    return _pool


async def init_schema() -> None:
    global _schema_initialized
    if _schema_initialized:
        if _status.get("state") == "connected":
            _update_status(state="ready", last_error=None)
        return

    async with _schema_lock:
        if _schema_initialized:
            if _status.get("state") == "connected":
                _update_status(state="ready", last_error=None)
            return
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                      email TEXT PRIMARY KEY,
                      name  TEXT,
                      title TEXT,
                      region TEXT,
                      created_at TIMESTAMPTZ DEFAULT NOW()
                    );
                    """
                )
        except Exception as exc:
            _update_status(state="error", last_error=exc.__class__.__name__)
            raise
        _schema_initialized = True
        _update_status(state="ready", last_error=None)
        _log.info("evt=db_schema_ready")


async def warmup_neon() -> None:
    try:
        await get_pool()
        await init_schema()
    except Exception as exc:  # pragma: no cover - warmup failures
        _update_status(state="error", last_error=exc.__class__.__name__)
        _log.error("evt=db_warmup_failed err=%s", exc.__class__.__name__)


def db_status() -> Dict[str, Any]:
    return dict(_status)


async def get_user(email: str) -> Optional[Dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT email, name, title, region FROM users WHERE email=$1",
            email,
        )
    if row is None:
        return None
    return {
        "email": row["email"],
        "name": row["name"],
        "title": row["title"],
        "region": row["region"],
    }


async def upsert_user(
    email: str,
    name: Optional[str],
    title: Optional[str],
    region: Optional[str],
) -> Dict[str, Any]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO users (email, name, title, region)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (email)
            DO UPDATE SET
              name = EXCLUDED.name,
              title = EXCLUDED.title,
              region = EXCLUDED.region
            RETURNING email, name, title, region;
            """,
            email,
            name,
            title,
            region,
        )
    return {
        "email": row["email"],
        "name": row["name"],
        "title": row["title"],
        "region": row["region"],
    }


def profile_complete(user: Dict[str, Any]) -> bool:
    if not user:
        return False
    return bool(user.get("name") and user.get("title") and user.get("region"))


__all__ = [
    "get_pool",
    "init_schema",
    "warmup_neon",
    "db_status",
    "get_user",
    "upsert_user",
    "profile_complete",
]
