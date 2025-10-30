"""Application configuration helpers."""

from __future__ import annotations

import json
import logging
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
ASR_IDLE_CLOSE_MS = 15000
ASR_TRACE = env_bool("ASR_TRACE", False)


def get_env(name: str, default=None):
    """Retrieve an environment variable with an optional default."""

    value = os.getenv(name)
    return value if value is not None else default


_log = logging.getLogger(__name__)

_ADMIN_SETTINGS_LOCK = threading.RLock()
_ADMIN_SETTINGS_CACHE: MutableMapping[str, Optional[Any]] = {}
_ADMIN_SETTINGS_STORE: Any = None  # Lazily initialised AdminSettingsStore or sentinel
_RUNTIME_FLAGS: MutableMapping[str, Any] = {}

_ALLOWED_ASR_INPUTS = {"webm_opus", "pcm_16k"}
_CAPTURE_TIMESLICE_MIN_MS = 20

_DEFAULT_POLICY_MEDIA = {
    "asr_input": "webm_opus",
    "asr_rate_hz": 48000,
    "asr_channels": 1,
    "fallbacks_allowed": False,
}

_DEFAULT_POLICY_CAPTURE = {
    "start_on_asr_ready": True,
    "start_on_turn_ready": True,
    "timeslice_ms": 200,
    "mask_during_tts": True,
}

_DEFAULT_POLICY_RECORDER = {
    "stop_on_tts_start": False,
    "mute_send_during_tts": True,
}

_DEFAULT_POLICY_INPUT = {
    "require_hotword_to_start": False,
}

_DEFAULT_POLICY_ASR = {
    "prearm_on_tts_end": True,
    "keep_stream_warm_ms": 30000,
}

_DEFAULT_POLICY_ROUTING = {
    "ws_version": "v2",
}

POLICY_MEDIA: MutableMapping[str, Any] = dict(_DEFAULT_POLICY_MEDIA)
POLICY_CAPTURE: MutableMapping[str, Any] = dict(_DEFAULT_POLICY_CAPTURE)
POLICY_RECORDER: MutableMapping[str, Any] = dict(_DEFAULT_POLICY_RECORDER)
POLICY_INPUT: MutableMapping[str, Any] = dict(_DEFAULT_POLICY_INPUT)
POLICY_ASR: MutableMapping[str, Any] = dict(_DEFAULT_POLICY_ASR)
POLICY_ROUTING: MutableMapping[str, Any] = dict(_DEFAULT_POLICY_ROUTING)
POLICY_OVERRIDES: MutableMapping[str, Any] = {
    "media": dict(POLICY_MEDIA),
    "capture": dict(POLICY_CAPTURE),
    "policy": {
        "recorder": dict(POLICY_RECORDER),
        "input": dict(POLICY_INPUT),
        "asr": dict(POLICY_ASR),
        "routing": dict(POLICY_ROUTING),
    },
}


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


def _coerce_db_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return _coerce_bool(value, default)
    if isinstance(value, Mapping):
        lowered = {str(k).lower(): v for k, v in value.items() if isinstance(k, str)}
        if "enabled" in lowered:
            return _coerce_db_bool(lowered["enabled"], default)
        if "value" in lowered:
            return _coerce_db_bool(lowered["value"], default)
    return bool(default)


def _coerce_db_int(
    value: Any, default: int, *, minimum: Optional[int] = None
) -> int:
    candidate: Optional[int] = None
    if isinstance(value, bool):
        candidate = int(value)
    elif isinstance(value, (int, float)):
        candidate = int(value)
    elif isinstance(value, str):
        return _coerce_int(value, default, minimum=minimum)
    elif isinstance(value, Mapping):
        lowered = {str(k).lower(): v for k, v in value.items() if isinstance(k, str)}
        for key in ("value", "count", "enabled"):
            if key in lowered:
                return _coerce_db_int(lowered[key], default, minimum=minimum)
    if candidate is None:
        candidate = int(default)
    if minimum is not None and candidate < minimum:
        return minimum
    return candidate


def _coerce_db_mapping(
    value: Any, default: Optional[Mapping[str, Any]] = None
) -> Mapping[str, Any]:
    base_default: Mapping[str, Any] = dict(default or {})

    if isinstance(value, Mapping):
        return {str(k): v for k, v in value.items()}

    if isinstance(value, memoryview):
        return _coerce_db_mapping(value.tobytes(), base_default)

    if isinstance(value, (bytes, bytearray)):
        try:
            decoded = value.decode("utf-8")
        except UnicodeDecodeError:
            decoded = value.decode("latin1", "ignore")
        return _coerce_db_mapping(decoded, base_default)

    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return dict(base_default)
        return _coerce_db_mapping(parsed, base_default)

    if isinstance(value, bool):
        return {"enabled": bool(value)}

    if isinstance(value, (int, float)):
        return {"value": value}

    if value is None:
        return dict(base_default)

    return dict(base_default)


def _sanitize_media_policy(
    value: Mapping[str, Any] | None,
    *,
    source: str,
    raw: Any,
) -> Mapping[str, Any]:
    sanitized = dict(_DEFAULT_POLICY_MEDIA)
    if not isinstance(value, Mapping):
        return sanitized

    asr_input_value = value.get("asr_input")
    if isinstance(asr_input_value, str):
        candidate = asr_input_value.strip()
        if candidate in _ALLOWED_ASR_INPUTS:
            sanitized["asr_input"] = candidate
        else:
            _log.warning(
                "evt=admin_settings_invalid_policy_media key=asr_input value=%s source=%s",
                candidate,
                source,
                extra={"component": "admin.settings", "raw": raw},
            )

    fallbacks_value = value.get("fallbacks_allowed")
    try:
        sanitized["fallbacks_allowed"] = _coerce_db_bool(
            fallbacks_value, sanitized["fallbacks_allowed"]
        )
    except ValueError:
        _log.warning(
            "evt=admin_settings_invalid_policy_media key=fallbacks_allowed source=%s",
            source,
            extra={"component": "admin.settings", "raw": raw},
        )

    rate_value = value.get("asr_rate_hz")
    sanitized["asr_rate_hz"] = _coerce_db_int(
        rate_value, sanitized["asr_rate_hz"], minimum=1
    )

    channels_value = value.get("asr_channels")
    sanitized["asr_channels"] = _coerce_db_int(
        channels_value, sanitized["asr_channels"], minimum=1
    )

    return sanitized


def _sanitize_capture_policy(
    value: Mapping[str, Any] | None,
    *,
    source: str,
    raw: Any,
) -> Mapping[str, Any]:
    sanitized = dict(_DEFAULT_POLICY_CAPTURE)
    if not isinstance(value, Mapping):
        return sanitized

    for key in ("start_on_asr_ready", "start_on_turn_ready", "mask_during_tts"):
        try:
            sanitized[key] = _coerce_db_bool(value.get(key), sanitized[key])
        except ValueError:
            _log.warning(
                "evt=admin_settings_invalid_policy_capture key=%s source=%s",
                key,
                source,
                extra={"component": "admin.settings", "raw": raw},
            )

    sanitized["timeslice_ms"] = _coerce_db_int(
        value.get("timeslice_ms"),
        sanitized["timeslice_ms"],
        minimum=_CAPTURE_TIMESLICE_MIN_MS,
    )

    return sanitized


def _sanitize_recorder_policy(
    value: Mapping[str, Any] | None,
    *,
    source: str,
    raw: Any,
) -> Mapping[str, Any]:
    sanitized = dict(_DEFAULT_POLICY_RECORDER)
    if not isinstance(value, Mapping):
        return sanitized

    for key in ("stop_on_tts_start", "mute_send_during_tts"):
        try:
            sanitized[key] = _coerce_db_bool(value.get(key), sanitized[key])
        except ValueError:
            _log.warning(
                "evt=admin_settings_invalid_policy_recorder key=%s source=%s",
                key,
                source,
                extra={"component": "admin.settings", "raw": raw},
            )

    return sanitized


def _sanitize_input_policy(
    value: Mapping[str, Any] | None,
    *,
    source: str,
    raw: Any,
) -> Mapping[str, Any]:
    sanitized = dict(_DEFAULT_POLICY_INPUT)
    if not isinstance(value, Mapping):
        return sanitized

    try:
        sanitized["require_hotword_to_start"] = _coerce_db_bool(
            value.get("require_hotword_to_start"), sanitized["require_hotword_to_start"]
        )
    except ValueError:
        _log.warning(
            "evt=admin_settings_invalid_policy_input key=require_hotword_to_start source=%s",
            source,
            extra={"component": "admin.settings", "raw": raw},
        )

    return sanitized


def _sanitize_asr_policy(
    value: Mapping[str, Any] | None,
    *,
    source: str,
    raw: Any,
) -> Mapping[str, Any]:
    sanitized = dict(_DEFAULT_POLICY_ASR)
    if not isinstance(value, Mapping):
        return sanitized

    for key in ("prearm_on_tts_end",):
        try:
            sanitized[key] = _coerce_db_bool(value.get(key), sanitized[key])
        except ValueError:
            _log.warning(
                "evt=admin_settings_invalid_policy_asr key=%s source=%s",
                key,
                source,
                extra={"component": "admin.settings", "raw": raw},
            )

    sanitized["keep_stream_warm_ms"] = _coerce_db_int(
        value.get("keep_stream_warm_ms"),
        sanitized["keep_stream_warm_ms"],
        minimum=0,
    )

    return sanitized


def _sanitize_routing_policy(
    value: Mapping[str, Any] | None,
    *,
    source: str,
    raw: Any,
) -> Mapping[str, Any]:
    sanitized = dict(_DEFAULT_POLICY_ROUTING)
    if not isinstance(value, Mapping):
        return sanitized

    ws_version = value.get("ws_version")
    if isinstance(ws_version, str) and ws_version.strip():
        candidate = ws_version.strip().lower()
        if candidate == "v2":
            sanitized["ws_version"] = "v2"
        else:
            _log.warning(
                "evt=admin_settings_invalid_policy_routing key=ws_version source=%s",
                source,
                extra={
                    "component": "admin.settings",
                    "raw": raw,
                    "configured_ws_version": ws_version,
                    "normalized_ws_version": "v2",
                },
            )
            sanitized["ws_version"] = "v2"
    elif ws_version is not None:
        _log.warning(
            "evt=admin_settings_invalid_policy_routing key=ws_version source=%s",
            source,
            extra={"component": "admin.settings", "raw": raw},
        )

    return sanitized


def _get_cached_admin_setting(key: str) -> Optional[Any]:
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


def update_admin_settings_cache(updates: Mapping[str, Optional[Any]]) -> None:
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


def get_admin_setting_raw(key: str) -> Optional[Any]:
    """Return the raw admin setting value for ``key`` when cached or stored."""

    return _get_cached_admin_setting(key)


def set_admin_setting_raw(
    key: str, value: Optional[Any], *, updated_by: str | None = None
) -> None:
    """Persist ``value`` for ``key`` and refresh runtime configuration."""

    normalized = _normalize_key(key)
    store = _get_admin_settings_store()
    if store is None:
        raise RuntimeError("Admin settings store unavailable")
    store.set(normalized, value, updated_by=updated_by)
    update_admin_settings_cache({normalized: value})


def _resolve_bool_setting(name: str, default: bool) -> tuple[bool, str, Any]:
    env_value = os.getenv(_env_name(name))
    if env_value is not None:
        return _coerce_bool(env_value, default), "env", env_value
    stored = _get_cached_admin_setting(name)
    if stored is not None:
        return _coerce_db_bool(stored, default), "db", stored
    return bool(default), "default", None


def bool_env_or_db(name: str, *, default: bool = False) -> bool:
    """Resolve a boolean config value from env, admin settings, or default."""

    value, _source, _raw = _resolve_bool_setting(name, default)
    return value


def _resolve_mapping_setting(
    name: str, default: Optional[Mapping[str, Any]] = None
) -> tuple[Mapping[str, Any], str, Any]:
    base_default: Mapping[str, Any] = dict(default or {})

    env_value = os.getenv(_env_name(name))
    if env_value is not None:
        try:
            parsed = json.loads(env_value)
        except json.JSONDecodeError:
            if env_value.strip():
                _log.warning(
                    "evt=admin_settings_env_parse_failed key=%s source=env",
                    name,
                    extra={"component": "admin.settings"},
                )
            return dict(base_default), "env", env_value
        normalized = _coerce_db_mapping(parsed, base_default)
        return normalized, "env", env_value

    stored = _get_cached_admin_setting(name)
    if stored is not None:
        return _coerce_db_mapping(stored, base_default), "db", stored

    return dict(base_default), "default", None


def _resolve_int_setting(
    name: str, default: int, *, minimum: Optional[int] = None
) -> tuple[int, str, Any]:
    env_value = os.getenv(_env_name(name))
    if env_value is not None:
        return _coerce_int(env_value, default, minimum=minimum), "env", env_value
    stored = _get_cached_admin_setting(name)
    if stored is not None:
        return _coerce_db_int(stored, default, minimum=minimum), "db", stored
    fallback = int(default)
    if minimum is not None and fallback < minimum:
        fallback = minimum
    return fallback, "default", None


def int_env_or_db(name: str, *, default: int = 0, minimum: Optional[int] = None) -> int:
    """Resolve an integer config value from env, admin settings, or default."""

    value, _source, _raw = _resolve_int_setting(name, default, minimum=minimum)
    return value


def reload_runtime_flags() -> None:
    """Refresh runtime configuration flags from env and admin settings."""

    diag_client_hud, hud_source, _hud_raw = _resolve_bool_setting(
        "diag_client_hud", default=False
    )
    audio_guardrails_default = {"enabled": True}
    audio_guardrails, guardrails_source, _guardrails_raw = _resolve_mapping_setting(
        "audio_guardrails", default=audio_guardrails_default
    )
    diag_audio_guard, audio_source, _audio_raw = _resolve_bool_setting(
        "diag_audio_guard", default=True
    )
    diag_chunk_sample_n, chunk_source, _chunk_raw = _resolve_int_setting(
        "diag_chunk_sample_n", default=10, minimum=1
    )

    policy_media_raw, media_source, media_raw = _resolve_mapping_setting(
        "policy_media", default=_DEFAULT_POLICY_MEDIA
    )
    policy_media = _sanitize_media_policy(
        policy_media_raw, source=media_source, raw=media_raw
    )

    policy_capture_raw, capture_source, capture_raw = _resolve_mapping_setting(
        "policy_capture", default=_DEFAULT_POLICY_CAPTURE
    )
    policy_capture = _sanitize_capture_policy(
        policy_capture_raw, source=capture_source, raw=capture_raw
    )

    policy_recorder_raw, recorder_source, recorder_raw = _resolve_mapping_setting(
        "policy_recorder", default=_DEFAULT_POLICY_RECORDER
    )
    policy_recorder = _sanitize_recorder_policy(
        policy_recorder_raw, source=recorder_source, raw=recorder_raw
    )

    policy_input_raw, input_source, input_raw = _resolve_mapping_setting(
        "policy_input", default=_DEFAULT_POLICY_INPUT
    )
    policy_input = _sanitize_input_policy(
        policy_input_raw, source=input_source, raw=input_raw
    )

    policy_asr_raw, asr_source, asr_raw = _resolve_mapping_setting(
        "policy_asr", default=_DEFAULT_POLICY_ASR
    )
    policy_asr = _sanitize_asr_policy(policy_asr_raw, source=asr_source, raw=asr_raw)

    policy_routing_raw, routing_source, routing_raw = _resolve_mapping_setting(
        "policy_routing", default=_DEFAULT_POLICY_ROUTING
    )
    policy_routing = _sanitize_routing_policy(
        policy_routing_raw, source=routing_source, raw=routing_raw
    )

    guardrails_value = dict(audio_guardrails)
    flags = {
        "DIAG_CLIENT_HUD": diag_client_hud,
        "AUDIO_GUARDRAILS": guardrails_value,
        "DIAG_AUDIO_GUARD": diag_audio_guard,
        "DIAG_CHUNK_SAMPLE_N": diag_chunk_sample_n,
        "POLICY_MEDIA": dict(policy_media),
        "POLICY_CAPTURE": dict(policy_capture),
        "POLICY_RECORDER": dict(policy_recorder),
        "POLICY_INPUT": dict(policy_input),
        "POLICY_ASR": dict(policy_asr),
        "POLICY_ROUTING": dict(policy_routing),
    }
    with _ADMIN_SETTINGS_LOCK:
        _RUNTIME_FLAGS.clear()
        _RUNTIME_FLAGS.update(flags)
    globals().update(flags)

    POLICY_MEDIA.clear()
    POLICY_MEDIA.update(policy_media)

    POLICY_CAPTURE.clear()
    POLICY_CAPTURE.update(policy_capture)

    POLICY_RECORDER.clear()
    POLICY_RECORDER.update(policy_recorder)

    POLICY_INPUT.clear()
    POLICY_INPUT.update(policy_input)

    POLICY_ASR.clear()
    POLICY_ASR.update(policy_asr)

    POLICY_ROUTING.clear()
    POLICY_ROUTING.update(policy_routing)

    POLICY_OVERRIDES.clear()
    POLICY_OVERRIDES.update({
        "media": dict(POLICY_MEDIA),
        "capture": dict(POLICY_CAPTURE),
        "policy": {
            "recorder": dict(POLICY_RECORDER),
            "input": dict(POLICY_INPUT),
            "asr": dict(POLICY_ASR),
            "routing": dict(POLICY_ROUTING),
        },
    })

    log_snapshot = [
        {"key": "DIAG_CLIENT_HUD", "value": diag_client_hud, "source": hud_source},
        {"key": "DIAG_AUDIO_GUARD", "value": diag_audio_guard, "source": audio_source},
        {
            "key": "AUDIO_GUARDRAILS",
            "value": guardrails_value,
            "source": guardrails_source,
        },
        {
            "key": "DIAG_CHUNK_SAMPLE_N",
            "value": diag_chunk_sample_n,
            "source": chunk_source,
        },
        {
            "key": "POLICY_MEDIA",
            "value": dict(policy_media),
            "source": media_source,
        },
        {
            "key": "POLICY_CAPTURE",
            "value": dict(policy_capture),
            "source": capture_source,
        },
        {
            "key": "POLICY_RECORDER",
            "value": dict(policy_recorder),
            "source": recorder_source,
        },
        {
            "key": "POLICY_INPUT",
            "value": dict(policy_input),
            "source": input_source,
        },
        {
            "key": "POLICY_ASR",
            "value": dict(policy_asr),
            "source": asr_source,
        },
        {
            "key": "POLICY_ROUTING",
            "value": dict(policy_routing),
            "source": routing_source,
        },
    ]
    _log.info(
        "evt=EVT_ADMIN_SETTINGS_LOAD snapshot=%s",
        log_snapshot,
        extra={"component": "admin.settings"},
    )


def get_client_config_snapshot() -> dict[str, Any]:
    """Return the current runtime flags exposed to browser clients."""

    with _ADMIN_SETTINGS_LOCK:
        return dict(_RUNTIME_FLAGS)


def set_admin_settings(
    values: Mapping[str, Optional[Any]], *, updated_by: str | None = None
) -> None:
    """Persist multiple admin settings and refresh runtime flags."""

    store = _get_admin_settings_store()
    if store is None:
        raise RuntimeError("Admin settings store unavailable")
    normalized_updates: dict[str, Optional[Any]] = {}
    for key, value in values.items():
        normalized = _normalize_key(key)
        store.set(normalized, value, updated_by=updated_by)
        normalized_updates[normalized] = value
    update_admin_settings_cache(normalized_updates)


reload_runtime_flags()


__all__ = [
    "ASR_BACKPRESSURE_THRESHOLD_BYTES",
    "ASR_IDLE_CLOSE_MS",
    "ASR_TRACE",
    "AUDIO_GUARDRAILS",
    "DEEPGRAM_API_KEY",
    "DIAG_AUDIO_GUARD",
    "DIAG_CHUNK_SAMPLE_N",
    "DIAG_CLIENT_HUD",
    "POLICY_MEDIA",
    "POLICY_CAPTURE",
    "POLICY_RECORDER",
    "POLICY_INPUT",
    "POLICY_ASR",
    "POLICY_ROUTING",
    "POLICY_OVERRIDES",
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
