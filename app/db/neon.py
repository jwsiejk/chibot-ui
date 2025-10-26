"""Neon database pool and helpers."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Optional
from urllib.parse import quote, urlsplit, urlunsplit

import asyncpg

_log = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None
_pool_lock = asyncio.Lock()


def _environment_dsn() -> str:
    for key in ("DATABASE_URL", "NEON_DATABASE_URL"):
        value = os.getenv(key)
        if value is not None:
            value_stripped = value.strip()
            if value_stripped:
                return value_stripped

    def _get_env(name: str) -> Optional[str]:
        raw = os.getenv(name)
        if raw is None:
            return None
        value = raw.strip()
        return value or None

    host = _get_env("PGHOST")
    database = _get_env("PGDATABASE")
    user = _get_env("PGUSER")
    password = _get_env("PGPASSWORD")
    port = _get_env("PGPORT") or "5432"

    missing = [
        name
        for name, val in (
            ("PGHOST", host),
            ("PGDATABASE", database),
            ("PGUSER", user),
            ("PGPASSWORD", password),
        )
        if not val
    ]
    if missing:
        raise RuntimeError(f"Missing Postgres configuration: {', '.join(missing)}")

    user_enc = quote(user, safe="")
    password_enc = quote(password, safe="")
    host_enc = host.strip()
    return (
        f"postgresql://{user_enc}:{password_enc}@{host_enc}:{port}/{database}?sslmode=require"
    )


def _redact_dsn(dsn: str) -> str:
    try:
        parsed = urlsplit(dsn)
    except ValueError:
        return dsn

    username = parsed.username
    password = parsed.password
    hostname = parsed.hostname or ""
    port_part = f":{parsed.port}" if parsed.port else ""
    host_display = hostname
    if host_display and ":" in host_display and not host_display.startswith("["):
        host_display = f"[{host_display}]"

    if username is None:
        netloc = parsed.netloc
    else:
        if password is None:
            userinfo = username
        else:
            userinfo = f"{username}:***"
        netloc = f"{userinfo}@{host_display}{port_part}"

    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


async def get_pool() -> asyncpg.Pool:
    """Return a lazily created asyncpg pool."""

    global _pool
    if _pool is None:
        async with _pool_lock:
            if _pool is None:
                dsn = _environment_dsn()
                _pool = await asyncpg.create_pool(
                    dsn,
                    statement_cache_size=0,
                    max_size=10,
                )
                _log.info("evt=db_pool_ready dsn=%s", _redact_dsn(dsn))
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
