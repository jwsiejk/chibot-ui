"""Admin settings API handlers."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Mapping, Optional

from app import config

_log = logging.getLogger(__name__)

_DEFAULT_SETTINGS: Dict[str, Any] = {"authentication": "none"}
_CHUNK_SAMPLE_MIN = 1
_CHUNK_SAMPLE_MAX = 100
_POLICY_MEDIA_ALLOWED_INPUTS = {"pcm_16k"}
_POLICY_AUDIO_ALLOWED_MODES = {"pcm16"}
_POLICY_CAPTURE_MIN_TIMESLICE = 20


async def handle_admin_settings(scope: dict, receive) -> "Response":
    """Serve and persist admin runtime settings."""

    method = _method(scope)
    if method == "OPTIONS":
        await _drain_body(receive)
        return _options_response()

    if method not in {"GET", "HEAD", "PATCH", "POST"}:
        await _drain_body(receive)
        return _json_response(status=405, error="method_not_allowed")

    if method in {"GET", "HEAD"}:
        await _drain_body(receive)
        return _respond_settings(method)

    body = await _read_body(receive)
    if not body:
        return _json_response(status=400, error="missing_body")

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _json_response(status=400, error="invalid_json")
    except Exception as exc:  # pragma: no cover - defensive logging
        _log.exception(
            "evt=admin_settings_parse_failed err=%s",
            exc.__class__.__name__,
            extra={"component": "admin.settings"},
        )
        return _json_response(status=400, error="invalid_json")

    updates = _extract_updates(payload)
    if updates is None:
        return _json_response(status=400, error="invalid_payload")
    if not updates:
        return _respond_settings(method)

    errors: Dict[str, str] = {}
    normalized: Dict[str, Any] = {}
    for key, value in updates.items():
        try:
            normalized[key] = _normalize_setting(key, value)
        except ValueError as exc:
            errors[key] = str(exc)

    if errors:
        return _json_response(status=400, error="invalid_settings", details=errors)

    try:
        config.set_admin_settings(normalized)
    except Exception as exc:  # pragma: no cover - defensive logging
        _log.exception(
            "evt=admin_settings_update_failed err=%s",
            exc.__class__.__name__,
            extra={"component": "admin.settings"},
        )
        return _json_response(status=500, error="update_failed")

    return _respond_settings(method)


def _extract_updates(payload: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, Mapping):
        return None
    settings = payload.get("settings")
    if settings is None:
        return None
    if not isinstance(settings, Mapping):
        return None
    updates: Dict[str, Any] = {}
    for key in (
        "diag_client_hud",
        "diag_audio_guard",
        "diag_chunk_sample_n",
        "policy_media",
        "policy_capture",
        "policy_recorder",
        "policy_input",
        "policy_asr",
        "policy_audio",
        "policy_routing",
    ):
        if key in settings:
            updates[key] = settings[key]
    return updates


def _normalize_policy_media(value: Any) -> Dict[str, Any]:
    current = dict(config.POLICY_MEDIA)
    if value is None:
        raise ValueError("expected_object")
    if isinstance(value, str):
        candidate = value.strip()
        if candidate not in _POLICY_MEDIA_ALLOWED_INPUTS:
            raise ValueError("invalid_asr_input")
        current["asr_input"] = candidate
        return current
    if not isinstance(value, Mapping):
        raise ValueError("expected_object")

    if "asr_input" in value:
        candidate_raw = value.get("asr_input")
        if not isinstance(candidate_raw, str):
            raise ValueError("invalid_asr_input")
        candidate = candidate_raw.strip()
        if candidate not in _POLICY_MEDIA_ALLOWED_INPUTS:
            raise ValueError("invalid_asr_input")
        current["asr_input"] = candidate

    if "fallbacks_allowed" in value:
        current["fallbacks_allowed"] = _coerce_bool(
            value.get("fallbacks_allowed"),
        )

    if "asr_rate_hz" in value:
        current["asr_rate_hz"] = _coerce_int(
            value.get("asr_rate_hz"), minimum=1, maximum=192000
        )

    if "asr_channels" in value:
        current["asr_channels"] = _coerce_int(
            value.get("asr_channels"), minimum=1, maximum=16
        )

    return current


def _normalize_policy_capture(value: Any) -> Dict[str, Any]:
    current = dict(config.POLICY_CAPTURE)
    if value is None:
        raise ValueError("expected_object")
    if not isinstance(value, Mapping):
        raise ValueError("expected_object")

    if "start_on_asr_ready" in value:
        current["start_on_asr_ready"] = _coerce_bool(value.get("start_on_asr_ready"))

    if "start_on_turn_ready" in value:
        current["start_on_turn_ready"] = _coerce_bool(value.get("start_on_turn_ready"))

    if "mask_during_tts" in value:
        current["mask_during_tts"] = _coerce_bool(value.get("mask_during_tts"))

    if "timeslice_ms" in value:
        current["timeslice_ms"] = _coerce_int(
            value.get("timeslice_ms"),
            minimum=_POLICY_CAPTURE_MIN_TIMESLICE,
            maximum=600000,
        )

    return current


def _normalize_policy_recorder(value: Any) -> Dict[str, Any]:
    current = dict(config.POLICY_RECORDER)
    if value is None:
        raise ValueError("expected_object")
    if not isinstance(value, Mapping):
        raise ValueError("expected_object")

    if "stop_on_tts_start" in value:
        current["stop_on_tts_start"] = _coerce_bool(value.get("stop_on_tts_start"))

    if "mute_send_during_tts" in value:
        current["mute_send_during_tts"] = _coerce_bool(
            value.get("mute_send_during_tts")
        )

    return current


def _normalize_policy_input(value: Any) -> Dict[str, Any]:
    current = dict(config.POLICY_INPUT)
    if value is None:
        raise ValueError("expected_object")
    if not isinstance(value, Mapping):
        raise ValueError("expected_object")

    if "require_hotword_to_start" in value:
        current["require_hotword_to_start"] = _coerce_bool(
            value.get("require_hotword_to_start")
        )

    return current


def _normalize_policy_asr(value: Any) -> Dict[str, Any]:
    current = dict(config.POLICY_ASR)
    if value is None:
        raise ValueError("expected_object")
    if not isinstance(value, Mapping):
        raise ValueError("expected_object")

    if "prearm_on_tts_end" in value:
        current["prearm_on_tts_end"] = _coerce_bool(value.get("prearm_on_tts_end"))

    if "keep_stream_warm_ms" in value:
        current["keep_stream_warm_ms"] = _coerce_int(
            value.get("keep_stream_warm_ms"), minimum=0, maximum=600000
        )

    if "commit_on_vad_silence" in value:
        current["commit_on_vad_silence"] = _coerce_bool(
            value.get("commit_on_vad_silence")
        )

    if "commit_silence_ms" in value:
        current["commit_silence_ms"] = _coerce_int(
            value.get("commit_silence_ms"), minimum=0, maximum=600000
        )

    if "max_utterance_ms" in value:
        current["max_utterance_ms"] = _coerce_int(
            value.get("max_utterance_ms"), minimum=0, maximum=600000
        )

    if "vendor" in value:
        vendor_value = value.get("vendor")
        if vendor_value is None:
            current["vendor"] = dict(config.POLICY_ASR.get("vendor", {}))
        elif isinstance(vendor_value, Mapping):
            vendor_block = dict(current.get("vendor", {}))
            if "primary" in vendor_value:
                primary = vendor_value.get("primary")
                if not isinstance(primary, str) or not primary.strip():
                    raise ValueError("expected_string")
                normalized = primary.strip().lower()
                if normalized not in {"speechmatics"}:
                    raise ValueError("unsupported_vendor")
                vendor_block["primary"] = normalized
            if "secondary" in vendor_value:
                secondary = vendor_value.get("secondary")
                if secondary is None:
                    vendor_block["secondary"] = None
                elif isinstance(secondary, str) and secondary.strip():
                    normalized_secondary = secondary.strip().lower()
                    if normalized_secondary not in {"speechmatics"}:
                        raise ValueError("unsupported_vendor")
                    vendor_block["secondary"] = normalized_secondary
                else:
                    raise ValueError("expected_string")
            current["vendor"] = vendor_block
        else:
            raise ValueError("expected_object")

    return current


def _normalize_policy_audio(value: Any) -> Dict[str, Any]:
    current = dict(config.POLICY_AUDIO)
    if value is None:
        raise ValueError("expected_object")
    if not isinstance(value, Mapping):
        raise ValueError("expected_object")

    if "pipeline" in value:
        pipeline_value = value.get("pipeline")
        if pipeline_value is None:
            current["pipeline"] = dict(config.POLICY_AUDIO.get("pipeline", {}))
        elif isinstance(pipeline_value, Mapping):
            pipeline_block = dict(current.get("pipeline", {}))
            if "mode" in pipeline_value:
                mode = pipeline_value.get("mode")
                if not isinstance(mode, str) or not mode.strip():
                    raise ValueError("expected_string")
                normalized = mode.strip().lower()
                if normalized not in _POLICY_AUDIO_ALLOWED_MODES:
                    raise ValueError("unsupported_mode")
                pipeline_block["mode"] = normalized
            current["pipeline"] = pipeline_block
        else:
            raise ValueError("expected_object")

    return current


def _normalize_policy_routing(value: Any) -> Dict[str, Any]:
    current = dict(config.POLICY_ROUTING)
    if value is None:
        raise ValueError("expected_object")
    if not isinstance(value, Mapping):
        raise ValueError("expected_object")

    if "ws_version" in value:
        candidate = value.get("ws_version")
        if not isinstance(candidate, str) or not candidate.strip():
            raise ValueError("expected_string")
        normalized = candidate.strip().lower()
        if normalized != "v2":
            raise ValueError("unsupported_ws_version")
        current["ws_version"] = "v2"

    return current


def _normalize_setting(key: str, value: Any) -> Optional[Any]:
    if value is None:
        return None
    if key == "diag_client_hud" or key == "diag_audio_guard":
        coerced = _coerce_bool(value)
        return "true" if coerced else "false"
    if key == "diag_chunk_sample_n":
        coerced = _coerce_int(value, minimum=_CHUNK_SAMPLE_MIN, maximum=_CHUNK_SAMPLE_MAX)
        return str(coerced)
    if key == "policy_media":
        return _normalize_policy_media(value)
    if key == "policy_capture":
        return _normalize_policy_capture(value)
    if key == "policy_recorder":
        return _normalize_policy_recorder(value)
    if key == "policy_input":
        return _normalize_policy_input(value)
    if key == "policy_asr":
        return _normalize_policy_asr(value)
    if key == "policy_audio":
        return _normalize_policy_audio(value)
    if key == "policy_routing":
        return _normalize_policy_routing(value)
    raise ValueError("unsupported_setting")


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        candidate = value.strip().lower()
        if candidate in {"1", "true", "t", "yes", "y", "on"}:
            return True
        if candidate in {"0", "false", "f", "no", "n", "off"}:
            return False
    raise ValueError("expected_boolean")


def _coerce_int(value: Any, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError("expected_integer")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        candidate = int(value)
    elif isinstance(value, str):
        try:
            candidate = int(float(value.strip()))
        except (ValueError, TypeError):
            raise ValueError("expected_integer")
    else:
        raise ValueError("expected_integer")

    if candidate < minimum:
        return minimum
    if candidate > maximum:
        return maximum
    return candidate


def _respond_settings(method: str) -> "Response":
    payload = {"settings": _current_settings()}
    if method == "HEAD":
        return _json_response(status=200, **payload, body_only=True)
    return _json_response(status=200, **payload)


def _current_settings() -> Dict[str, Any]:
    settings = dict(_DEFAULT_SETTINGS)
    settings.update(
        {
            "diag_client_hud": bool(config.DIAG_CLIENT_HUD),
            "diag_audio_guard": bool(config.DIAG_AUDIO_GUARD),
            "diag_chunk_sample_n": int(config.DIAG_CHUNK_SAMPLE_N),
            "policy_media": dict(config.POLICY_MEDIA),
            "policy_capture": dict(config.POLICY_CAPTURE),
            "policy_recorder": dict(config.POLICY_RECORDER),
            "policy_input": dict(config.POLICY_INPUT),
            "policy_asr": dict(config.POLICY_ASR),
            "policy_audio": dict(config.POLICY_AUDIO),
            "policy_routing": dict(config.POLICY_ROUTING),
        }
    )
    return settings


async def _read_body(receive) -> bytes:
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message.get("type") != "http.request":
            break
        body = message.get("body", b"") or b""
        if body:
            chunks.append(body)
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


async def _drain_body(receive) -> None:
    while True:
        message = await receive()
        if message.get("type") != "http.request":
            break
        if not message.get("more_body", False):
            break


def _method(scope: Mapping[str, Any]) -> str:
    value = scope.get("method")
    if isinstance(value, bytes):
        value = value.decode("latin1", "ignore")
    if not isinstance(value, str):
        return "GET"
    return value.upper()


def _options_response() -> "Response":
    from app.asgi_gateway import Response

    headers = (
        (b"content-length", b"0"),
        (b"allow", b"GET,HEAD,PATCH,POST,OPTIONS"),
    )
    return Response(status=204, body=b"", headers=headers)


def _json_response(*, status: int, body_only: bool = False, **payload: Any) -> "Response":
    from app.asgi_gateway import Response, json_response

    if body_only:
        body = json_response(status=status, **payload).body
        headers = (
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("ascii")),
        )
        return Response(status=status, body=b"", headers=headers)
    return json_response(status=status, **payload)


__all__ = ["handle_admin_settings"]
