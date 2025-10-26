"""Application configuration helpers."""

from __future__ import annotations

import os
import threading
from typing import Any, Mapping, MutableMapping, Optional

try:
    from dotenv import load_dotenv  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None
else:  # pragma: no branch
    load_dotenv(dotenv_path=".env", override=False)


def env_bool(name: str, default: bool = False) -> bool:
    """Return an environment variable parsed as a boolean."""

    value = os.getenv(name)
    if value is None:
        return bool(default)
    value = value.strip().lower()
    if value in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "f", "no", "n", "off"}:
        return False
    return bool(default)


DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
ASR_BACKPRESSURE_THRESHOLD_BYTES = int(
    os.getenv("ASR_BACKPRESSURE_THRESHOLD_BYTES", "1048576")
)
ASR_IDLE_CLOSE_MS = int(os.getenv("ASR_IDLE_CLOSE_MS", "4000"))
ASR_TRACE = env_bool("ASR_TRACE", False)


def get_env(name: str, default=None):
    """Retrieve an environment variable with an optional default."""

    value = os.getenv(name)
    return value if value is not None else default


_ADMIN_SETTINGS_LOCK = threading.RLock()
_ADMIN_SETTINGS_CACHE: MutableMapping[str, Optional[str]] = {}
_ADMIN_SETTINGS_STORE: Any = None  # Lazily initialised AdminSettingsStore or sentinel
_RUNTIME_FLAGS: MutableMapping[str, Any] = {}


def _normalize_key(key: str) -> str:
    return key.strip().lower()


def _env_name(key: str) -> str:
    return _normalize_key(key).upper()


def _get_admin_settings_store():
    global _ADMIN_SETTINGS_STORE
    if _ADMIN_SETTINGS_STORE is False:
        return None
    if _ADMIN_SETTINGS_STORE is None:
        try:  # pragma: no cover - import guarded for optional deps
            from app.db.admin_settings import AdminSettingsStore
        except Exception:  # pragma: no cover - defensive import guard
            _ADMIN_SETTINGS_STORE = False
            return None
        _ADMIN_SETTINGS_STORE = AdminSettingsStore()
    return _ADMIN_SETTINGS_STORE


def _coerce_bool(value: str, default: bool) -> bool:
    candidate = value.strip().lower()
    if candidate in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if candidate in {"0", "false", "f", "no", "n", "off"}:
        return False
    return bool(default)


def _coerce_int(value: str, default: int, *, minimum: Optional[int] = None) -> int:
    try:
        candidate = int(value.strip())
    except (AttributeError, TypeError, ValueError):
        return int(default)
    if minimum is not None and candidate < minimum:
        return minimum
    return candidate


def _get_cached_admin_setting(key: str) -> Optional[str]:
    normalized = _normalize_key(key)
    with _ADMIN_SETTINGS_LOCK:
        if normalized in _ADMIN_SETTINGS_CACHE:
            return _ADMIN_SETTINGS_CACHE[normalized]
    store = _get_admin_settings_store()
    if store is None:
        with _ADMIN_SETTINGS_LOCK:
            _ADMIN_SETTINGS_CACHE.setdefault(normalized, None)
        return None
    value = store.get(normalized)
    with _ADMIN_SETTINGS_LOCK:
        _ADMIN_SETTINGS_CACHE[normalized] = value
    return value


def update_admin_settings_cache(updates: Mapping[str, Optional[str]]) -> None:
    """Merge ``updates`` into the local admin settings cache and refresh flags."""

    changed = False
    with _ADMIN_SETTINGS_LOCK:
        for raw_key, raw_value in updates.items():
            normalized = _normalize_key(raw_key)
            if _ADMIN_SETTINGS_CACHE.get(normalized) != raw_value:
                _ADMIN_SETTINGS_CACHE[normalized] = raw_value
                changed = True
    if changed:
        reload_runtime_flags()


def get_admin_setting_raw(key: str) -> Optional[str]:
    """Return the raw admin setting value for ``key`` when cached or stored."""

    return _get_cached_admin_setting(key)


def set_admin_setting_raw(key: str, value: Optional[str]) -> None:
    """Persist ``value`` for ``key`` and refresh runtime configuration."""

    normalized = _normalize_key(key)
    store = _get_admin_settings_store()
    if store is None:
        raise RuntimeError("Admin settings store unavailable")
    store.set(normalized, value)
    update_admin_settings_cache({normalized: value})


def bool_env_or_db(name: str, *, default: bool = False) -> bool:
    """Resolve a boolean config value from env, admin settings, or default."""

    env_value = os.getenv(_env_name(name))
    if env_value is not None:
        return _coerce_bool(env_value, default)
    stored = _get_cached_admin_setting(name)
    if stored is not None:
        return _coerce_bool(stored, default)
    return bool(default)


def int_env_or_db(name: str, *, default: int = 0, minimum: Optional[int] = None) -> int:
    """Resolve an integer config value from env, admin settings, or default."""

    env_value = os.getenv(_env_name(name))
    if env_value is not None:
        return _coerce_int(env_value, default, minimum=minimum)
    stored = _get_cached_admin_setting(name)
    if stored is not None:
        return _coerce_int(stored, default, minimum=minimum)
    if minimum is not None and default < minimum:
        return minimum
    return int(default)


def reload_runtime_flags() -> None:
    """Refresh runtime configuration flags from env and admin settings."""

    flags = {
        "DIAG_CLIENT_HUD": bool_env_or_db("diag_client_hud", default=False),
        "DIAG_AUDIO_GUARD": bool_env_or_db("diag_audio_guard", default=True),
        "DIAG_CHUNK_SAMPLE_N": int_env_or_db(
            "diag_chunk_sample_n", default=10, minimum=1
        ),
    }
    with _ADMIN_SETTINGS_LOCK:
        _RUNTIME_FLAGS.clear()
        _RUNTIME_FLAGS.update(flags)
    globals().update(flags)


def get_client_config_snapshot() -> dict[str, Any]:
    """Return the current runtime flags exposed to browser clients."""

    with _ADMIN_SETTINGS_LOCK:
        return dict(_RUNTIME_FLAGS)


def set_admin_settings(values: Mapping[str, Optional[str]]) -> None:
    """Persist multiple admin settings and refresh runtime flags."""

    store = _get_admin_settings_store()
    if store is None:
        raise RuntimeError("Admin settings store unavailable")
    normalized_updates: dict[str, Optional[str]] = {}
    for key, value in values.items():
        normalized = _normalize_key(key)
        store.set(normalized, value)
        normalized_updates[normalized] = value
    update_admin_settings_cache(normalized_updates)


reload_runtime_flags()


__all__ = [
    "ASR_BACKPRESSURE_THRESHOLD_BYTES",
    "ASR_IDLE_CLOSE_MS",
    "ASR_TRACE",
    "DEEPGRAM_API_KEY",
    "DIAG_AUDIO_GUARD",
    "DIAG_CHUNK_SAMPLE_N",
    "DIAG_CLIENT_HUD",
    "bool_env_or_db",
    "get_admin_setting_raw",
    "get_client_config_snapshot",
    "get_env",
    "int_env_or_db",
    "reload_runtime_flags",
    "set_admin_setting_raw",
    "set_admin_settings",
    "update_admin_settings_cache",
]
