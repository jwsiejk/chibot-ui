"""Persistence helpers for the ``admin_settings`` table."""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from app.db.postgres_config import get_postgres_config

try:  # pragma: no cover - optional dependency guard
    import psycopg  # type: ignore
    from psycopg import errors as psy_errors  # type: ignore
    from psycopg import sql  # type: ignore
except Exception:  # pragma: no cover - defensive fallback when psycopg missing
    psycopg = None  # type: ignore
    psy_errors = None  # type: ignore
    sql = None  # type: ignore


_log = logging.getLogger(__name__)

_SettingsValue = Optional[str]
_ConnectionFactory = Callable[[], "psycopg.Connection"]  # type: ignore[name-defined]


def _detect_column_error(exc: Exception) -> bool:
    """Return ``True`` when the exception indicates a missing column."""

    if psy_errors is None:  # pragma: no cover - dependency missing in runtime
        return False

    if isinstance(exc, psy_errors.UndefinedColumn):
        return True

    pgcode = getattr(exc, "pgcode", None)
    if pgcode in {getattr(psy_errors, "UNDEFINED_COLUMN", None)}:
        return True

    return False


def _coerce_value(value: object) -> _SettingsValue:
    """Normalize database values to plain strings."""

    if value is None:
        return None
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.decode("latin1", "ignore")
    return str(value)


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
        self._memory: dict[str, str] = {}
        self._lock = threading.RLock()
        self._key_column: str | None = None
        self._value_column: str | None = None

    def _get_connection(self):
        if self._conn_factory is None:
            return None
        try:
            conn = self._conn_factory()
        except Exception as exc:  # pragma: no cover - connection failures
            _log.warning(
                "evt=admin_settings_conn_failed err=%s", exc, extra={"component": "admin.settings"}
            )
            return None
        try:
            if hasattr(conn, "autocommit"):
                conn.autocommit = True  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - attribute missing
            pass
        return conn

    def _resolve_columns(self, cursor) -> tuple[str, str]:
        """Determine the key/value column names for the admin settings table."""

        if self._key_column and self._value_column:
            return self._key_column, self._value_column

        if sql is None:  # pragma: no cover - dependency missing
            raise RuntimeError("psycopg is required to resolve admin_settings columns")

        candidate_keys = ("key", "settings_key", "name", "setting_key")
        candidate_values = ("value", "settings_value", "setting_value")

        available: set[str] = set()

        try:
            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = %s
                  AND table_schema = current_schema()
                """,
                ("admin_settings",),
            )
        except Exception as exc:
            if not _detect_column_error(exc):  # pragma: no cover - defensive
                raise
        else:
            available = {row[0] for row in cursor.fetchall()}

        key_column = next((name for name in candidate_keys if name in available), None)
        value_column = next((name for name in candidate_values if name in available), None)

        if key_column is None or value_column is None:
            # As a last resort, attempt to probe using trial queries so legacy
            # deployments without information_schema privileges continue to work.
            if key_column is None:
                for probe_key in candidate_keys:
                    query = sql.SQL(
                        "SELECT {key} FROM admin_settings LIMIT 0"
                    ).format(key=sql.Identifier(probe_key))
                    try:
                        cursor.execute(query)
                    except Exception as exc:
                        if _detect_column_error(exc):
                            continue
                        raise
                    else:
                        key_column = probe_key
                        break

            if value_column is None:
                for probe_value in candidate_values:
                    query = sql.SQL(
                        "SELECT {value} FROM admin_settings LIMIT 0"
                    ).format(value=sql.Identifier(probe_value))
                    try:
                        cursor.execute(query)
                    except Exception as exc:
                        if _detect_column_error(exc):
                            continue
                        raise
                    else:
                        value_column = probe_value
                        break

        if key_column is None or value_column is None:
            raise RuntimeError(
                "Unable to determine admin_settings key/value column names"
            )

        self._key_column = key_column
        self._value_column = value_column
        return key_column, value_column

    def _select(self, cursor, key: str):
        key_column, value_column = self._resolve_columns(cursor)
        if sql is None:  # pragma: no cover - dependency missing
            raise RuntimeError("psycopg is required to query admin_settings")

        query = sql.SQL(
            "SELECT {value} FROM admin_settings WHERE {key} = %s LIMIT 1"
        ).format(value=sql.Identifier(value_column), key=sql.Identifier(key_column))

        cursor.execute(query, (key,))
        return cursor.fetchone()

    def _upsert(self, cursor, key: str, value: str | None) -> None:
        key_column, value_column = self._resolve_columns(cursor)
        if sql is None:  # pragma: no cover - dependency missing
            raise RuntimeError("psycopg is required to modify admin_settings")

        if value is None:
            query = sql.SQL(
                "DELETE FROM admin_settings WHERE {key} = %s"
            ).format(key=sql.Identifier(key_column))
            cursor.execute(query, (key,))
            return
        query = sql.SQL(
            "INSERT INTO admin_settings ({key}, {value}) VALUES (%s, %s) "
            "ON CONFLICT ({key}) DO UPDATE SET {value} = EXCLUDED.{value}"
        ).format(
            key=sql.Identifier(key_column),
            value=sql.Identifier(value_column),
        )
        cursor.execute(query, (key, value))

    def get(self, key: str) -> _SettingsValue:
        """Return the string value for ``key`` or ``None`` when unset."""

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

        return _coerce_value(row[0]) if row else None

    def set(self, key: str, value: str | None) -> None:
        """Persist ``value`` for ``key`` in the admin settings store."""

        if value is not None:
            coerced = _coerce_value(value)
            if coerced is None:
                coerced = ""
            value = coerced

        conn = self._get_connection()
        if conn is None:
            if not self._allow_memory_fallback:
                raise RuntimeError("AdminSettingsStore has no database connection")
            with self._lock:
                if value is None:
                    self._memory.pop(key, None)
                else:
                    self._memory[key] = value
            return

        try:
            with conn.cursor() as cursor:
                self._upsert(cursor, key, value)
        finally:
            try:
                conn.close()
            except Exception:  # pragma: no cover - defensive
                pass

    def snapshot(self) -> dict[str, str]:
        """Return a best-effort in-memory snapshot of cached settings."""

        with self._lock:
            return dict(self._memory)


__all__ = ["AdminSettingsStore"]
