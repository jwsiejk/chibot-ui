"""Shared Postgres DSN normalisation utilities."""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit


_log = logging.getLogger(__name__)

_DEFAULT_SSLMODE = "require"
_DEFAULT_CHANNEL_BINDING = "require"

_VALID_SSLMODES = {
    "disable",
    "allow",
    "prefer",
    "require",
    "verify-ca",
    "verify-full",
}

_VALID_CHANNEL_BINDINGS = {
    "disable",
    "prefer",
    "require",
}


@dataclass(frozen=True)
class NormalizedDSN:
    """Container for a normalised DSN and its relevant options."""

    dsn: str
    sslmode: str
    channel_binding: str


@dataclass(frozen=True)
class PostgresConfig:
    """Encapsulates the effective database configuration."""

    main: NormalizedDSN
    admin: NormalizedDSN
    env_sslmode: str
    env_channel_binding: str


_CONFIG: Optional[PostgresConfig] = None
_CONFIG_LOCK = threading.Lock()


def get_postgres_config() -> PostgresConfig:
    """Return the cached, normalised Postgres configuration."""

    global _CONFIG
    if _CONFIG is None:
        with _CONFIG_LOCK:
            if _CONFIG is None:
                _CONFIG = _load_postgres_config()
    return _CONFIG


def redact_dsn(dsn: str) -> str:
    """Return ``dsn`` with credentials removed for logging."""

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


def _load_postgres_config() -> PostgresConfig:
    env_sslmode, env_channel_binding = _normalize_pg_environment()

    main_dsn, main_sources = _resolve_primary_dsn(env_sslmode, env_channel_binding)
    admin_dsn, admin_sources = _resolve_admin_dsn(main_dsn, env_sslmode, env_channel_binding)

    for key in main_sources:
        os.environ[key] = main_dsn.dsn
    # Ensure DATABASE_URL always reflects the canonical DSN.
    os.environ.setdefault("DATABASE_URL", main_dsn.dsn)
    os.environ["DATABASE_URL"] = main_dsn.dsn
    if "NEON_DATABASE_URL" in os.environ:
        os.environ["NEON_DATABASE_URL"] = main_dsn.dsn

    for key in admin_sources:
        os.environ[key] = admin_dsn.dsn

    _log.info(
        "evt=db_dsn_sanitized main=%s admin=%s main_sslmode=%s main_channel_binding=%s "
        "admin_sslmode=%s admin_channel_binding=%s",
        redact_dsn(main_dsn.dsn),
        redact_dsn(admin_dsn.dsn),
        main_dsn.sslmode,
        main_dsn.channel_binding,
        admin_dsn.sslmode,
        admin_dsn.channel_binding,
    )

    _log.info(
        "evt=pg_env PGSSLMODE=%s PGCHANNELBINDING=%s",
        env_sslmode,
        env_channel_binding,
    )

    return PostgresConfig(
        main=main_dsn,
        admin=admin_dsn,
        env_sslmode=env_sslmode,
        env_channel_binding=env_channel_binding,
    )


def _normalize_pg_environment() -> Tuple[str, str]:
    sslmode = _normalize_env_option("PGSSLMODE", (), _VALID_SSLMODES, _DEFAULT_SSLMODE)
    channel_binding = _normalize_env_option(
        "PGCHANNELBINDING",
        ("PGCHANNEL_BINDING",),
        _VALID_CHANNEL_BINDINGS,
        _DEFAULT_CHANNEL_BINDING,
    )
    return sslmode, channel_binding


def _normalize_env_option(
    primary: str,
    aliases: Sequence[str],
    valid_values: Iterable[str],
    default: str,
) -> str:
    values = [primary, *aliases]
    raw_value: Optional[str] = None
    source_key: Optional[str] = None
    for key in values:
        candidate = os.environ.get(key)
        if candidate is not None:
            raw_value = candidate
            source_key = key
            break

    normalized, requires_default = _normalize_option_value(raw_value, valid_values, default)

    if source_key is not None and source_key != primary:
        # Collapse aliases into the canonical variable.
        os.environ.pop(source_key, None)

    for alias in aliases:
        os.environ.pop(alias, None)

    os.environ[primary] = normalized

    if raw_value is not None:
        cleaned = _clean_option(raw_value)
        comparable = cleaned.lower() if cleaned is not None else None
        if comparable != normalized or requires_default or _needs_warning(raw_value):
            _log.warning(
                "evt=pg_env_sanitized var=%s before=%s after=%s",
                primary,
                raw_value,
                normalized,
            )

    return normalized


def _needs_warning(raw_value: str) -> bool:
    stripped = raw_value.strip()
    if stripped != raw_value:
        return True
    if stripped.strip("'\"") != stripped:
        return True
    if any(ch in raw_value for ch in ("\r", "\n")):
        return True
    return False


def _normalize_option_value(
    raw_value: Optional[str],
    valid_values: Iterable[str],
    default: str,
) -> Tuple[str, bool]:
    cleaned = _clean_option(raw_value)
    if cleaned is None:
        return default, True

    candidate = cleaned.lower()
    if candidate not in set(valid_values):
        return default, True
    return candidate, False


def _clean_option(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    without_newlines = value.replace("\r", "").replace("\n", "")
    trimmed = without_newlines.strip()
    trimmed = trimmed.strip("'\"")
    trimmed = trimmed.strip()
    collapsed = "".join(ch for ch in trimmed if not ch.isspace())
    return collapsed or None


def _resolve_primary_dsn(
    env_sslmode: str,
    env_channel_binding: str,
) -> Tuple[NormalizedDSN, Sequence[str]]:
    env_candidates = ["DATABASE_URL", "NEON_DATABASE_URL"]
    for key in env_candidates:
        raw = os.environ.get(key)
        if raw:
            normalized = _normalize_dsn(raw, env_sslmode, env_channel_binding)
            return normalized, (key,)

    constructed = _build_dsn_from_parts(env_sslmode, env_channel_binding)
    return constructed, ()


def _resolve_admin_dsn(
    default: NormalizedDSN,
    env_sslmode: str,
    env_channel_binding: str,
) -> Tuple[NormalizedDSN, Sequence[str]]:
    admin_keys = ("ADMIN_DATABASE_URL", "ADMIN_SETTINGS_DATABASE_URL")
    for key in admin_keys:
        raw = os.environ.get(key)
        if raw:
            normalized = _normalize_dsn(raw, env_sslmode, env_channel_binding)
            return normalized, (key,)

    return NormalizedDSN(default.dsn, default.sslmode, default.channel_binding), ()


def _build_dsn_from_parts(
    env_sslmode: str,
    env_channel_binding: str,
) -> NormalizedDSN:
    host = _normalize_generic_env("PGHOST")
    database = _normalize_generic_env("PGDATABASE")
    user = _normalize_generic_env("PGUSER")
    password = _normalize_generic_env("PGPASSWORD")
    port = _normalize_generic_env("PGPORT") or "5432"

    missing = [
        name
        for name, value in (
            ("PGHOST", host),
            ("PGDATABASE", database),
            ("PGUSER", user),
            ("PGPASSWORD", password),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing Postgres configuration: {', '.join(missing)}")

    user_enc = quote(user, safe="")
    password_enc = quote(password, safe="")
    host_enc = host.strip()

    query = urlencode(
        (
            ("sslmode", env_sslmode),
            ("channel_binding", env_channel_binding),
        )
    )

    dsn = f"postgresql://{user_enc}:{password_enc}@{host_enc}:{port}/{database}?{query}"
    return NormalizedDSN(dsn, env_sslmode, env_channel_binding)


def _normalize_generic_env(name: str) -> Optional[str]:
    raw = os.environ.get(name)
    if raw is None:
        return None
    cleaned = raw.replace("\r", "").replace("\n", "").strip()
    if not cleaned:
        os.environ.pop(name, None)
        return None
    if cleaned != raw:
        os.environ[name] = cleaned
    return cleaned


def _normalize_dsn(
    raw_dsn: str,
    env_sslmode: str,
    env_channel_binding: str,
) -> NormalizedDSN:
    stripped = raw_dsn.strip()
    try:
        parsed = urlsplit(stripped)
    except ValueError:
        return NormalizedDSN(stripped, env_sslmode, env_channel_binding)

    params = []
    sslmode = None
    channel_binding = None
    changed = stripped != raw_dsn

    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() == "sslmode":
            normalized_value, _ = _normalize_option_value(value, _VALID_SSLMODES, env_sslmode)
            if normalized_value != value:
                changed = True
            sslmode = normalized_value
            params.append(("sslmode", normalized_value))
        elif key.lower() == "channel_binding":
            normalized_value, _ = _normalize_option_value(
                value, _VALID_CHANNEL_BINDINGS, env_channel_binding
            )
            if normalized_value != value:
                changed = True
            channel_binding = normalized_value
            params.append(("channel_binding", normalized_value))
        else:
            params.append((key, value))

    if sslmode is None:
        sslmode = env_sslmode
        params.append(("sslmode", sslmode))
    if channel_binding is None:
        channel_binding = env_channel_binding
        params.append(("channel_binding", channel_binding))

    new_query = urlencode(params, doseq=True)
    if new_query != parsed.query:
        changed = True

    if not changed:
        return NormalizedDSN(stripped, sslmode, channel_binding)

    sanitized = urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            new_query,
            parsed.fragment,
        )
    )
    return NormalizedDSN(sanitized, sslmode, channel_binding)


__all__ = [
    "NormalizedDSN",
    "PostgresConfig",
    "get_postgres_config",
    "redact_dsn",
]

