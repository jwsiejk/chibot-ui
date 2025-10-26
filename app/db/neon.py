"""Neon database pool and helpers."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

import asyncpg

from app.db.postgres_config import get_postgres_config, redact_dsn

_log = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None
_pool_lock = asyncio.Lock()


async def get_pool() -> asyncpg.Pool:
    """Return a lazily created asyncpg pool."""

    global _pool
    if _pool is None:
        async with _pool_lock:
            if _pool is None:
                config = get_postgres_config()
                dsn = config.main.dsn
                _pool = await asyncpg.create_pool(
                    dsn,
                    statement_cache_size=0,
                    max_size=10,
                )
                _log.info("evt=db_pool_ready dsn=%s", redact_dsn(dsn))
    assert _pool is not None
    return _pool


async def init_schema() -> None:
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
    "get_user",
    "upsert_user",
    "profile_complete",
]
