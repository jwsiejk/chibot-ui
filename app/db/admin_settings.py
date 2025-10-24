"""Persistence helpers for the ``admin_settings`` table."""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from app.config import get_env

try:  # pragma: no cover - optional dependency guard
    import psycopg  # type: ignore
    from psycopg import errors as psy_errors  # type: ignore
except Exception:  # pragma: no cover - defensive fallback when psycopg missing
    psycopg = None  # type: ignore
    psy_errors = None  # type: ignore


_LOGGER = logging.getLogger(__name__)

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

    dsn = get_env("DATABASE_URL")
    if not dsn:
        return None

    def _factory() -> "psycopg.Connection":  # type: ignore[name-defined]
        return psycopg.connect(dsn)  # type: ignore[call-arg]

    return _factory


class AdminSettingsStore:
    """Lightweight helper for reading and writing admin settings."""

    _QUERY_PRIMARY = "SELECT value FROM admin_settings WHERE key = %s LIMIT 1"
    _UPSERT_PRIMARY = (
        "INSERT INTO admin_settings (key, value) VALUES (%s, %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
    )
    _DELETE_PRIMARY = "DELETE FROM admin_settings WHERE key = %s"

    _QUERY_FALLBACK = (
        "SELECT settings_value FROM admin_settings WHERE settings_key = %s LIMIT 1"
    )
    _UPSERT_FALLBACK = (
        "INSERT INTO admin_settings (settings_key, settings_value) VALUES (%s, %s) "
        "ON CONFLICT (settings_key) DO UPDATE SET settings_value = EXCLUDED.settings_value"
    )
    _DELETE_FALLBACK = "DELETE FROM admin_settings WHERE settings_key = %s"

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

    def _get_connection(self):
        if self._conn_factory is None:
            return None
        try:
            conn = self._conn_factory()
        except Exception as exc:  # pragma: no cover - connection failures
            _LOGGER.warning("AdminSettingsStore failed to connect: %s", exc)
            return None
        try:
            if hasattr(conn, "autocommit"):
                conn.autocommit = True  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - attribute missing
            pass
        return conn

    def _select(self, cursor, key: str):
        try:
            cursor.execute(self._QUERY_PRIMARY, (key,))
        except Exception as exc:
            if _detect_column_error(exc):
                cursor.execute(self._QUERY_FALLBACK, (key,))
            else:
                raise
        return cursor.fetchone()

    def _upsert(self, cursor, key: str, value: str | None) -> None:
        if value is None:
            try:
                cursor.execute(self._DELETE_PRIMARY, (key,))
            except Exception as exc:
                if _detect_column_error(exc):
                    cursor.execute(self._DELETE_FALLBACK, (key,))
                else:
                    raise
            return
        try:
            cursor.execute(self._UPSERT_PRIMARY, (key, value))
        except Exception as exc:
            if _detect_column_error(exc):
                cursor.execute(self._UPSERT_FALLBACK, (key, value))
            else:
                raise

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
