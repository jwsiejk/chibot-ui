"""Persistence helpers for the ``admin_settings`` table."""
from __future__ import annotations

import json
import logging
import threading
from typing import Callable, Optional

from app.db.postgres_config import get_postgres_config

try:  # pragma: no cover - optional dependency guard
    import psycopg  # type: ignore
except Exception:  # pragma: no cover - defensive fallback when psycopg missing
    psycopg = None  # type: ignore


_log = logging.getLogger(__name__)

_SettingsValue = Optional[object]
_ConnectionFactory = Callable[[], "psycopg.Connection"]  # type: ignore[name-defined]


def _decode_json_value(value: object) -> object:
    """Best-effort conversion from database payloads to Python objects."""

    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            value = value.decode("latin1", "ignore")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _prepare_json_value(value: object | None) -> object | None:
    """Parse string values into JSON when possible."""

    if value is None:
        return None
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return ""
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return value
    return value


def _build_default_factory() -> _ConnectionFactory | None:
    """Create a connection factory from environment configuration when possible."""

    if psycopg is None:  # pragma: no cover - dependency missing
        return None

    try:
        dsn = get_postgres_config().admin.dsn
    except RuntimeError:
        return None

    def _factory() -> "psycopg.Connection":  # type: ignore[name-defined]
        return psycopg.connect(dsn)  # type: ignore[call-arg]

    return _factory


class AdminSettingsStore:
    """Lightweight helper for reading and writing admin settings."""

    def __init__(
        self,
        conn_factory: _ConnectionFactory | None = None,
        *,
        allow_memory_fallback: bool = True,
    ) -> None:
        self._conn_factory = conn_factory or _build_default_factory()
        self._allow_memory_fallback = allow_memory_fallback
        self._memory: dict[str, object] = {}
        self._lock = threading.RLock()

    def _get_connection(self):
        if self._conn_factory is None:
            return None
        try:
            conn = self._conn_factory()
        except Exception as exc:  # pragma: no cover - connection failures
            _log.warning(
                "evt=admin_settings_conn_failed err=%s",
                exc,
                extra={"component": "admin.settings"},
            )
            return None
        try:
            if hasattr(conn, "autocommit"):
                conn.autocommit = True  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - attribute missing
            pass
        return conn

    def _select(self, cursor, key: str):
        cursor.execute(
            "SELECT value_jsonb FROM admin_settings WHERE key = %s LIMIT 1",
            (key,),
        )
        return cursor.fetchone()

    def _upsert(
        self, cursor, key: str, value: object | None, updated_by: str | None
    ) -> None:
        if value is None:
            cursor.execute("DELETE FROM admin_settings WHERE key = %s", (key,))
            return
        payload = json.dumps(value)
        cursor.execute(
            """
            INSERT INTO admin_settings (key, value_jsonb, updated_by)
            VALUES (%s, %s::jsonb, %s)
            ON CONFLICT (key) DO UPDATE SET
                value_jsonb = EXCLUDED.value_jsonb,
                updated_at = NOW(),
                updated_by = COALESCE(EXCLUDED.updated_by, admin_settings.updated_by),
                version = admin_settings.version + 1
            """,
            (key, payload, updated_by),
        )

    def get(self, key: str) -> _SettingsValue:
        """Return the JSON value for ``key`` or ``None`` when unset."""

        conn = self._get_connection()
        if conn is None:
            if not self._allow_memory_fallback:
                return None
            with self._lock:
                return self._memory.get(key)

        try:
            with conn.cursor() as cursor:
                row = self._select(cursor, key)
        finally:
            try:
                conn.close()
            except Exception:  # pragma: no cover - defensive
                pass

        if not row:
            return None
        return _decode_json_value(row[0])

    def set(
        self, key: str, value: object | None, *, updated_by: str | None = None
    ) -> None:
        """Persist ``value`` for ``key`` in the admin settings store."""

        normalized_value = _prepare_json_value(value)

        conn = self._get_connection()
        if conn is None:
            if not self._allow_memory_fallback:
                raise RuntimeError("AdminSettingsStore has no database connection")
            with self._lock:
                if normalized_value is None:
                    self._memory.pop(key, None)
                else:
                    self._memory[key] = normalized_value
            return

        try:
            with conn.cursor() as cursor:
                self._upsert(cursor, key, normalized_value, updated_by)
        finally:
            try:
                conn.close()
            except Exception:  # pragma: no cover - defensive
                pass

    def snapshot(self) -> dict[str, object]:
        """Return a best-effort in-memory snapshot of cached settings."""

        with self._lock:
            return dict(self._memory)


__all__ = ["AdminSettingsStore"]
